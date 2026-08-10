#!/usr/bin/env python3
"""Guard: the repayment time axis says the same thing on screen and in print.

    python3 check_chart_axes.py     (exit 1 on a violation)

WHY THIS EXISTS. The balance and payment charts carried an axis labelled
"Years" whose ticks were bare floats. A capped federal tranche clearing at 2.4
years is a real event on that chart, and "2.4" is a number the reader has to
convert -- while the loan is billed monthly and every servicer quotes months.
Years and months replaced it.

That change has to land in FOUR chart pairs -- balance, payment, and the two
comparison variants -- each a Plotly chart plus a hand-written matplotlib twin
with no shared drawing code. The chart-twin rule in CLAUDE.md exists because
that arrangement has already drifted once (the balance chart's principal/unpaid
interest split shipped on the Plotly side alone). A tick FORMAT is exactly the
kind of change that drifts silently: both charts still render, both are still
correct about the data, and only the labels disagree.

So the ticks are computed by one helper and this asserts both renderers use it,
by building both and comparing what they came out with.

It also pins the two layout facts that were found by rendering rather than by
reasoning:

  * The last tick must be INSIDE the plotted range. Plotly centres a tick label
    on its tick and clips at the plot-area edge, so a label sitting exactly on
    the edge loses its right half and is dropped entirely -- a 10-year Standard
    plan drew a line to an edge whose last mark said 8y.
  * A 10-year plan's schedule ends at month 120 but a 30-year one's last row can
    sit just short of the round year, so the axis end is nudged to the next tick
    only when the gap is a rounding artefact, never far enough to leave years of
    blank plot.

WHAT IT CANNOT DO. It compares tick TEXT, not pixels: two charts can agree on
every label and still lay them out unreadably. Look at the rendered output.
"""
import sys

APP = "app.py"

# Lengths a calendar actually has: months up to a quarter and a half-year, then
# whole years. Written out here rather than read from app.py's own constant --
# see the note in check_ladder.
CALENDAR_STEPS_MONTHS = {1, 2, 3, 6} | {12 * n for n in range(1, 21)}


def app_namespace():
    """app.py's sections 1-2. Same exec-prefix trick as the other guards."""
    src = open(APP).read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "axescheck"}
    exec(compile(prefix, APP, "exec"), ns)
    return ns


def check_format(ns, fail):
    """fmt_duration's boundaries, which are where a duration formatter goes
    wrong: the year rollover and the two cases that must NOT print a zero."""
    fmt = ns["fmt_duration"]
    for months, expected in ((0, "0"), (1, "1m"), (8, "8m"), (11, "11m"),
                             (12, "1y"), (13, "1y 1m"), (24, "2y"),
                             (29, "2y 5m"), (120, "10y")):
        got = fmt(months)
        if got != expected:
            fail(f"fmt_duration({months}) is {got!r}, expected {expected!r}")
    # "0y 8m" reads as a template that failed to fill in, and "2y 0m" is a
    # whole year wearing a suffix that says it is not one.
    for months in (1, 8, 11):
        if "y" in fmt(months):
            fail(f"fmt_duration({months}) prints a year it does not have: {fmt(months)!r}")
    for months in (12, 24, 120):
        if "m" in fmt(months):
            fail(f"fmt_duration({months}) prints a zero month: {fmt(months)!r}")


def check_ladder(ns, fail):
    """Tick counts stay sane from a seven-month payoff to a thirty-year one,
    and every step is a unit a calendar actually has.

    The 1/2/5 ladder that suits a money axis does not suit time: a 4-month or
    5-month step is not a length anyone counts in.
    """
    ticks = ns["duration_ticks"]
    for span in (0.25, 0.58, 1.0, 1.4, 2.4, 4.0, 7.0, 10.0, 15.0, 20.0, 25.0, 30.0):
        vals, txt = ticks([0, span])
        if not vals:
            fail(f"a {span}-year span produced no ticks")
            continue
        if len(vals) > 8:
            fail(f"a {span}-year span produced {len(vals)} ticks -- an axis, not a ruler")
        if len(vals) < 3:
            fail(f"a {span}-year span produced only {len(vals)} ticks")
        steps = {round((b - a) * 12) for a, b in zip(vals, vals[1:])}
        # Against a LITERAL, not against DURATION_TICK_MONTHS. Reading the
        # step set out of the constant it is meant to police only asserts
        # that the ladder equals itself -- swapping in a money-style
        # 1/2/5/10/20 ladder passed this check untouched, which is the same
        # derive-from-the-thing-you-are-testing flaw CLAUDE.md records
        # against the residency guard.
        if not steps <= CALENDAR_STEPS_MONTHS:
            fail(f"a {span}-year span steps by {sorted(steps)} months. A tick "
                 f"step has to be a length a calendar has -- a quarter, a half "
                 f"year, a year, or a multiple of years; 5 or 10 MONTHS is not "
                 f"a unit anyone counts in.")
    # Degenerate inputs must return empty rather than raise: a zero-length
    # schedule is reachable (a loan already repaid) and must cost a tick, not
    # a page.
    for values in ([], [float("nan")], [0], [None]):
        if ticks(values) != ([], []):
            fail(f"duration_ticks({values!r}) should be empty, got {ticks(values)}")


def check_axis_end(ns, fail):
    """The nudge closes a rounding gap and never buys a label with blank plot."""
    end = ns["duration_axis_end"]
    ticks = ns["duration_ticks"]
    # Month 120 of a 10-year plan lands at 9.92 on some schedules and 10.0 on
    # others; both must finish on the 10y tick.
    for data_max in (9.92, 10.0):
        vals, _ = ticks([0, data_max])
        if end(data_max, vals) != 10.0:
            fail(f"a {data_max}-year payoff should close on the 10y tick, "
                 f"got {end(data_max, vals)}")
    # 26 years on a five-year ladder must NOT stretch to 30.
    vals, _ = ticks([0, 26.0])
    if end(26.0, vals) != 26.0:
        fail(f"a 26-year payoff was stretched to {end(26.0, vals)} -- four "
             f"blank years to gain one label")


def check_twins(ns, fail):
    """THE ONE THIS FILE IS FOR. Every chart pair, same ticks, same title.

    Built from real simulator output rather than a synthetic frame, because
    the schedules are what the drift would be about.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fed = ns["calculate_standard_repayment"](27000, 6.5, 3)
    priv = ns["calculate_standard_repayment"](19489, 9.0, 15)
    long_loan = ns["calculate_standard_repayment"](60000, 6.5, 10)
    short_loan = ns["calculate_standard_repayment"](8000, 5.0, 2)

    def plotly_axis(fig):
        return list(fig.layout.xaxis.ticktext or []), fig.layout.xaxis.title.text

    def pdf_axis(build, *args, **kwargs):
        """The matplotlib twins return a reportlab Image, so the axis is read
        by intercepting the figure on its way there."""
        captured = {}
        original = ns["_pdf_image_from_figure"]

        def spy(fig, max_width=None, **kw):
            ax = fig.axes[0]
            captured["ticks"] = [t.get_text() for t in ax.get_xticklabels()]
            captured["title"] = ax.get_xlabel()
            return original(fig, max_width=max_width) if max_width else original(fig)

        ns["_pdf_image_from_figure"] = spy
        try:
            build(*args, **kwargs)
        finally:
            ns["_pdf_image_from_figure"] = original
            plt.close("all")
        return captured.get("ticks", []), captured.get("title")

    pairs = [
        ("balance",
         lambda: ns["build_balance_chart"](long_loan["schedule"], "Standard"),
         lambda: pdf_axis(ns["build_pdf_balance_chart"], long_loan["schedule"], "Standard")),
        ("balance (short)",
         lambda: ns["build_balance_chart"](short_loan["schedule"], "Standard"),
         lambda: pdf_axis(ns["build_pdf_balance_chart"], short_loan["schedule"], "Standard")),
        ("payment",
         lambda: ns["build_payment_chart"](long_loan, "Standard"),
         lambda: pdf_axis(ns["build_pdf_payment_chart"], long_loan, "Standard")),
        ("payment (two tranches)",
         lambda: ns["build_payment_chart"](fed, "Standard", federal_result=fed,
                                           private_result=priv),
         lambda: pdf_axis(ns["build_pdf_payment_chart"], fed, "Standard",
                          federal_result=fed, private_result=priv)),
        ("comparison balance",
         lambda: ns["build_comparison_balance_chart"](
             long_loan["schedule"], "A", short_loan["schedule"], "B"),
         lambda: pdf_axis(ns["build_pdf_comparison_balance_chart"],
                          long_loan["schedule"], "A", short_loan["schedule"], "B")),
        ("comparison payment",
         lambda: ns["build_comparison_payment_chart"](long_loan, "A", short_loan, "B"),
         lambda: pdf_axis(ns["build_pdf_comparison_payment_chart"],
                          long_loan, "A", short_loan, "B")),
    ]

    for name, screen, printed in pairs:
        fig = screen()
        if fig is None:
            fail(f"{name}: the on-screen chart did not build")
            continue
        screen_ticks, screen_title = plotly_axis(fig)
        pdf_ticks, pdf_title = printed()
        if not screen_ticks:
            fail(f"{name}: the on-screen chart has no duration ticks -- "
                 f"apply_duration_axis was not called")
        if screen_ticks != pdf_ticks:
            fail(f"{name}: the twins disagree about the ticks\n"
                 f"      screen: {screen_ticks}\n"
                 f"      print:  {pdf_ticks}")
        if screen_title != pdf_title:
            fail(f"{name}: the twins disagree about the axis title "
                 f"({screen_title!r} vs {pdf_title!r})")
        if screen_title != ns["DURATION_AXIS_TITLE"]:
            fail(f"{name}: axis title is {screen_title!r}, expected "
                 f"{ns['DURATION_AXIS_TITLE']!r}")

        # The last tick must be inside the range, or Plotly drops its label.
        low, high = fig.layout.xaxis.range
        last = max(fig.layout.xaxis.tickvals)
        if last >= high:
            fail(f"{name}: the last tick sits at {last} on a range ending "
                 f"{high} -- Plotly clips a label centred on the plot edge, so "
                 f"that tick renders as nothing")
        if low != 0:
            fail(f"{name}: the range starts at {low}; there is no time before "
                 f"repayment begins")


def main() -> int:
    ns = app_namespace()
    problems = []
    fail = problems.append
    check_format(ns, fail)
    check_ladder(ns, fail)
    check_axis_end(ns, fail)
    check_twins(ns, fail)

    if problems:
        print(f"chart axes: {len(problems)} problem(s)\n")
        print("\n".join(f"  {p}" for p in problems))
        print("\n  A tick format that drifts between the twins is invisible: "
              "both charts\n  still render and both are still right about the "
              "data. Only the labels\n  disagree, and nobody holds a PDF beside "
              "a screen to notice.")
        return 1
    print("chart axes OK -- 6 chart pairs agree on ticks and title, the "
          "years-and-months\n  ladder holds from three months to thirty years, "
          "and every last tick is\n  inside its own plot area.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

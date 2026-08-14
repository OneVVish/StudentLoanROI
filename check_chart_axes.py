#!/usr/bin/env python3
"""Guard: the money and time axes say the same thing on screen and in print.

    python3 check_chart_axes.py     (exit 1 on a violation)

THE MONEY HALF. The same drift, one axis over. Every money axis in the app is
labelled by ONE formatter (fmt_money_k) reached two ways -- Plotly through
money_k_ticks, matplotlib through _PDF_MONEY_K_FORMATTER -- because Plotly picks
its own SI prefix per axis and put two charts on one page in two different
units. A renderer that formats its own ticks would look right in isolation and
disagree with its twin, which nobody holds side by side.

It also pins the unit ladder itself. Thousands-forever printed "$5,000k" on the
35-year net-position chart and "$3,690k" on a 30-year premium: five digits and a
suffix where the reader wants a magnitude. Past a million the unit is millions,
and the k and M ranges may not overlap -- "$1,000k" beside "$1.0M" on one axis
is the same two-units failure in miniature.

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


# What each magnitude must READ AS, written out rather than derived from
# fmt_money_k's own threshold constant -- reading the boundary out of the code
# under test only asserts that it equals itself, the flaw check_ladder records
# above and CLAUDE.md records against the residency guard. These are the
# boundaries by hand: the last value in dollars, the first in thousands, the
# last in thousands, the first in millions, and the sign on each side.
MONEY_LABELS = (
    (0, "$0"), (500, "$500"), (999, "$999"), (-750, "-$750"),
    # 12,600 rather than 12,500: a half-thousand is a rounding tie, and
    # Python rounds those to even, so the expectation would be asserting the
    # formatting library rather than this app's ladder.
    (1000, "$1k"), (12600, "$13k"), (-49000, "-$49k"), (250000, "$250k"),
    (999000, "$999k"), (999499, "$999k"),
    # The switch is where thousands would need four digits, not at 1,000,000.
    (999500, "$1.0M"), (1000000, "$1.0M"), (1200000, "$1.2M"),
    (3690000, "$3.7M"), (-3690000, "-$3.7M"), (5000000, "$5.0M"),
    (12300000, "$12.3M"),
)


def check_money_format(ns, fail):
    """fmt_money_k at every unit boundary, and the two things a money tick
    must never be: four digits wearing a k, or a bare Plotly SI prefix."""
    fmt = ns["fmt_money_k"]
    for value, expected in MONEY_LABELS:
        got = fmt(value)
        if got != expected:
            fail(f"fmt_money_k({value}) is {got!r}, expected {expected!r}")
    # None is reachable -- a chart series with a gap -- and must cost a label,
    # not a page.
    if fmt(None) != "":
        fail(f"fmt_money_k(None) is {fmt(None)!r}, expected an empty label")


def check_money_ladder(ns, fail):
    """Across every span a chart in this app can carry, no tick may be written
    in four digits of thousands, and one axis may not mix the two units."""
    ticks = ns["money_k_ticks"]
    spans = (5_000, 60_000, 190_000, 469_900, 900_000, 1_000_000, 1_200_000,
             2_500_000, 3_690_000, 5_030_000, 12_000_000)
    for high in spans:
        vals, txt = ticks([0, high])
        if not txt:
            fail(f"a $0-${high:,} span produced no money ticks")
            continue
        for label in txt:
            digits = label.lstrip("-$").rstrip("kM").replace(",", "").split(".")[0]
            if label.endswith("k") and len(digits) > 3:
                fail(f"a $0-${high:,} span labels a tick {label!r}. Four digits "
                     f"and a suffix is not a magnitude anyone reads -- past a "
                     f"million the unit is millions.")
        if any(t.endswith("k") for t in txt) and any(t.endswith("M") for t in txt):
            # Legal only in the direction k-then-M: the ladder is monotonic, so
            # the units may change once and must not change back.
            units = [t[-1] if t[-1] in "kM" else "$" for t in txt]
            ordered = [u for i, u in enumerate(units) if i == 0 or u != units[i - 1]]
            if [u for u in ordered if u in "kM"] != ["k", "M"]:
                fail(f"a $0-${high:,} span changes unit more than once: {txt}")
    # Degenerate input costs ticks, not a page -- the same rule the time axis
    # carries.
    for values in ([], [None], [float("nan")]):
        if ticks(values) != ([], []):
            fail(f"money_k_ticks({values!r}) should be empty, got {ticks(values)}")


def check_money_twins(ns, fail):
    """THE MONEY HALF OF THE ONE THIS FILE IS FOR: both renderers label a money
    axis through the SAME formatter.

    Tick POSITIONS are deliberately not compared. Plotly is handed explicit
    ticks (money_k_ticks) while matplotlib keeps its own locator, so equal
    positions were never the invariant -- equal LABELLING is. So each side is
    asked what it wrote at the positions it chose, and every label must be
    fmt_money_k of its own position.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    fmt = ns["fmt_money_k"]

    # The formatter object the PDF charts install, asked directly. A twin that
    # grew its own lambda would pass every other check in this file.
    for value, expected in MONEY_LABELS:
        got = ns["_PDF_MONEY_K_FORMATTER"](value, 0)
        if got != expected:
            fail(f"_PDF_MONEY_K_FORMATTER({value}) is {got!r}, expected "
                 f"{expected!r} -- the print twin is not going through "
                 f"fmt_money_k")

    # A real chart pair, at a scale that crosses the million: a 35-year net
    # position is where "$5,000k" was actually rendered.
    frame = pd.DataFrame([
        {"year": y, "Series": s,
         "Net Position": base * y * 1.0}
        for s, base in (("Medicine", 150_000), ("High School Graduate", 60_000))
        for y in range(1, 36)
    ])
    fig = ns["build_net_position_chart"](frame, 10, "Years after graduation")
    screen = list(fig.layout.yaxis.ticktext or [])
    screen_vals = list(fig.layout.yaxis.tickvals or [])
    if not screen:
        fail("the net-position chart has no explicit money ticks -- "
             "money_k_ticks was not called")
    if screen != [fmt(v) for v in screen_vals]:
        fail(f"the on-screen money axis is not labelled by fmt_money_k: "
             f"{screen} at {screen_vals}")

    captured = {}
    original = ns["_pdf_image_from_figure"]

    def spy(pdf_fig, max_width=None, **kw):
        ax = pdf_fig.axes[0]
        pdf_fig.canvas.draw()
        captured["labels"] = [t.get_text() for t in ax.get_yticklabels()]
        captured["positions"] = list(ax.get_yticks())
        return original(pdf_fig, max_width=max_width) if max_width else original(pdf_fig)

    ns["_pdf_image_from_figure"] = spy
    try:
        ns["build_pdf_net_position_chart"](frame, 10, "Years after graduation")
    finally:
        ns["_pdf_image_from_figure"] = original
        plt.close("all")

    labels = [t for t in captured.get("labels", []) if t]
    if not labels:
        fail("the printed net-position chart produced no money labels")
    for label, position in zip(captured.get("labels", []),
                               captured.get("positions", [])):
        if label and label != fmt(position):
            fail(f"the printed money axis wrote {label!r} at {position} where "
                 f"fmt_money_k says {fmt(position)!r} -- the twins disagree "
                 f"about the unit")
    if any(t.endswith("k") and len(t.lstrip("-$").rstrip("k").replace(",", "")) > 3
           for t in labels):
        fail(f"the printed net-position chart still labels in four-digit "
             f"thousands: {labels}")


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
    check_money_format(ns, fail)
    check_money_ladder(ns, fail)
    check_money_twins(ns, fail)

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
          "every last tick is inside\n  its own plot area, and both renderers "
          "label money through one formatter\n  that switches to millions "
          "before any tick can print four digits of\n  thousands.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

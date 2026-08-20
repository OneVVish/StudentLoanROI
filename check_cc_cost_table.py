#!/usr/bin/env python3
"""Guard: the per-state community-college cost table is complete, keyed the way
its consumers key it, and free of order-of-magnitude typos.

    python3 check_cc_cost_table.py        (exit 1 on a violation)

WHY THIS ONE EXISTS. COMMUNITY_COLLEGE_COST_BY_STATE is the only price table in
this app that is HAND-TYPED rather than produced by a pipeline. The wage data,
the Scorecard costs and the graduate tuition all come out of a script that can
be re-run and diffed; these fifty numbers were entered once from the NCES Digest
via the Education Data Initiative, and nothing regenerates or cross-checks them.

That matters because of what the figure does. It is not decoration: it is the
per-year price of the community-college leg, and on a 2+2 path the entire
benefit the model reports comes from tuition avoided during those years. A
mistyped state moves every scenario for that state and nothing on screen looks
wrong -- California at $1,390 is the lowest entry in the table by a wide margin
and is exactly the sort of value a future editor might "correct" upward, or
that a stray zero could turn into $13,900.

Six things are asserted, each a distinct way this regresses:

1. **Every state in the dropdown has a price.** US_STATES is what the selector
   offers, so a state present there and absent here silently serves the
   national default while the caption claims a state figure.
2. **No key is unknown to US_STATES.** A typo like "CAA" is INERT rather than
   broken: the lookup never matches, the dropdown never offers it, and the
   entry sits there looking complete. Same failure shape this codebase already
   records for an occupation title that does not exist.
3. **The keys match the app's other state-keyed tables.** The table's own
   comment says it is keyed to match STATE_TAX_BRACKETS and CITY_DATA's
   `state_key`, and every state a CITY_DATA metro sits in must resolve, since
   the city is what seeds the community-college state.
4. **Every value is inside a sane band.** A ten-times typo is the specific
   error a hand-typed table invites, and it is invisible in a scenario: the
   number is plausible in isolation and only wrong against its neighbours.
5. **The national default is inside the same band and is not an outlier**, so
   a state falling back to it cannot be handed something absurd.
6. **The fallback actually falls back.** community_college_cost_for_state must
   return the national figure for None, for "" and for an unknown key rather
   than raising or returning 0 -- a zero here would price the CC leg as free
   and make the 2+2 path look better than any real school.

BANDS ARE LITERALS, deliberately. Deriving them from the table's own min and
max would assert only that the table equals itself, which is the flaw recorded
against the first versions of check_chart_axes and the residency guard. The
figures below are in-district tuition and fees, so the floor allows for a state
that has made community college close to free and the ceiling is well above the
dearest entry.

NOTE ON WHAT THIS CANNOT CHECK. It cannot tell whether a number is CURRENT.
Nothing in the repo records the vintage beyond a source comment, so a table
that is five years stale passes every assertion here. Re-derive from NCES when
the wage and Scorecard vintages move.
"""
import ast
import sys
from pathlib import Path

APP = str(Path(__file__).resolve().parent / "app.py")

# In-district tuition and fees, per year. Not a cost of attendance: no housing,
# no food, no books. The floor is low on purpose -- California's fee waiver
# puts it at $1,390 -- and the ceiling sits well clear of the dearest state.
COST_FLOOR = 500
# TIGHTENED after a negative control walked through the first ceiling. At
# $15,000 a ten-times typo on California ($1,390 -> $13,900) passed every
# assertion, which is precisely the error this band exists to catch. In-district
# tuition and fees is a narrow quantity: the dearest state in the table is
# $8,000, so $10,000 leaves real headroom and still traps an extra zero on all
# fifty entries.
COST_CEILING = 10_000
# The out-of-state rate is a different quantity and needs its own ceiling:
# it runs to $17,740 in Tennessee, which is real rather than a typo.
OUT_OF_STATE_CEILING = 30_000
# NO RELATIVE OUTLIER CHECK, and that is a deliberate deletion rather than an
# omission. One was written -- "no state above 3x the table median" -- and it
# was DEAD: the median is $4,790, so it could only fire above $14,370, which the
# $10,000 ceiling already rejects. An assertion that cannot fail is worse than
# no assertion, because it reads like coverage. Tightening the multiple until it
# fires would leave it policing a few hundred dollars either side of the
# ceiling, which the ceiling does better.
#
# WHAT THIS THEREFORE DOES NOT CATCH: a wrong number that is merely plausible.
# California at $2,800 instead of $1,390 passes everything here. The band is an
# order-of-magnitude net, not a fact-check, and only re-deriving from NCES can
# do the latter.


def load_app_namespace():
    """app.py's sections 1-2 plus its later pure functions, without the UI.

    Same exec-prefix trick analyze_model.py uses; see CLAUDE.md on why the
    section banners are load-bearing.
    """
    src = open(APP).read()
    cut = src.index("# 3. PAGE CONFIG & SESSION STATE")
    prefix = src[:src.rindex("# " + "=" * 60, 0, cut)]
    ns = {"__name__": "cccostcheck"}
    exec(compile(prefix, APP, "exec"), ns)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name not in ns:
            exec(compile(ast.Module(body=[node], type_ignores=[]), APP, "exec"), ns)
    return ns


def check_coverage(ns):
    """Every state the dropdown offers has a price, and no key is a typo."""
    problems = []
    table, states = ns["COMMUNITY_COLLEGE_COST_BY_STATE"], ns["US_STATES"]

    missing = sorted(set(states) - set(table))
    if missing:
        problems.append(
            f"  {len(missing)} state(s) in the dropdown have no price and fall "
            f"back to the national average while the caption names their "
            f"state: {', '.join(missing)}")

    unknown = sorted(set(table) - set(states))
    if unknown:
        problems.append(
            f"  {len(unknown)} key(s) are not states the dropdown offers, so "
            f"the entry can never be reached and looks complete sitting there: "
            f"{', '.join(unknown)}")
    return problems


def check_keying(ns):
    """The table is keyed the way its consumers are keyed."""
    problems = []
    table = ns["COMMUNITY_COLLEGE_COST_BY_STATE"]

    # Every metro's state must resolve. The selected city is what seeds the
    # community-college state, so a metro in a state this table lacks quietly
    # prices that visitor's CC years at the national figure.
    metro_states = {info.get("state_key") for info in ns["CITY_DATA"].values()
                    if info.get("state_key")}
    unpriced = sorted(metro_states - set(table))
    if unpriced:
        problems.append(
            f"  CITY_DATA has metros in {', '.join(unpriced)}, which this table "
            f"does not price, so those visitors silently get the national "
            f"average for their community-college years")

    # The table's own comment claims alignment with the tax brackets.
    brackets = ns.get("STATE_TAX_BRACKETS") or {}
    if brackets:
        both = set(table) & set(brackets)
        if not both:
            problems.append(
                "  no key is shared with STATE_TAX_BRACKETS, so the two "
                "state-keyed tables have drifted apart in spelling")
    return problems


def check_values(ns):
    """No order-of-magnitude typos, and the fallback is inside the same band."""
    problems = []
    table = ns["COMMUNITY_COLLEGE_COST_BY_STATE"]
    default = ns["COMMUNITY_COLLEGE_COA_DEFAULT"]

    for state, cost in sorted(table.items()):
        if not isinstance(cost, (int, float)):
            problems.append(f"  {state} is {cost!r}, not a number")
            continue
        if not (COST_FLOOR <= cost <= COST_CEILING):
            problems.append(
                f"  {state} is ${cost:,} a year, outside the ${COST_FLOOR:,} to "
                f"${COST_CEILING:,} band this table can plausibly hold. A "
                f"ten-times typo is the error a hand-typed table invites and it "
                f"is invisible in a scenario")

    if not (COST_FLOOR <= default <= COST_CEILING):
        problems.append(
            f"  the national default is ${default:,}, outside the same band, so "
            f"any state falling back to it gets an implausible figure")
    return problems


def check_fallback(ns):
    """An unknown or absent state resolves to the national figure, never 0."""
    problems = []
    resolve = ns["community_college_cost_for_state"]
    default = ns["COMMUNITY_COLLEGE_COA_DEFAULT"]

    for absent in (None, "", "__national__", "ZZ"):
        got = resolve(absent)
        if got != default:
            problems.append(
                f"  community_college_cost_for_state({absent!r}) returned "
                f"{got!r} rather than the national ${default:,}. A zero here "
                f"prices the community-college leg as free, which makes the "
                f"2+2 path beat every real school")

    # And a known state must NOT fall back, or the table is inert.
    if resolve("CA") == default and ns["COMMUNITY_COLLEGE_COST_BY_STATE"].get("CA") != default:
        problems.append(
            "  a known state resolved to the national default, so the lookup "
            "is not reading the table at all")
    return problems


def check_residency(ns):
    """The IPEDS file carries both rates, and a non-resident is never cheaper."""
    problems = []
    table = ns["load_cc_costs"]()
    if not table:
        problems.append(
            "  data/cc_costs_clean.csv did not load, so every state falls back "
            "to the hand-typed in-district dict and the non-resident rate is "
            "the national median for everybody. Rebuild it with "
            "build_cc_costs.py")
        return problems

    for state, rates in sorted(table.items()):
        if rates["out"] < rates["in"]:
            problems.append(
                f"  {state} prices a non-resident (${rates['out']:,.0f}) BELOW "
                f"a resident (${rates['in']:,.0f}), which no community college "
                f"does. The two columns are probably swapped")
        for label, value in (("in", rates["in"]), ("out", rates["out"])):
            if not (COST_FLOOR <= value <= OUT_OF_STATE_CEILING):
                problems.append(
                    f"  {state} {label}-rate is ${value:,.0f}, outside the "
                    f"${COST_FLOOR:,} to ${OUT_OF_STATE_CEILING:,} band")

    # THE DEFECT THIS WHOLE PATH EXISTS TO REMOVE. A non-resident in a state
    # the file does not cover must fall back to a NON-RESIDENT figure. Falling
    # back to the in-district default would price them as a local, silently,
    # which is exactly what the app did before this file existed.
    resolve = ns["community_college_cost_for_state"]
    in_default = ns["COMMUNITY_COLLEGE_COA_DEFAULT"]
    for absent in (None, "ZZ"):
        got = resolve(absent, False)
        if got <= in_default:
            problems.append(
                f"  a non-resident with no state data resolved to ${got:,}, at "
                f"or below the in-district default of ${in_default:,}. That "
                f"prices a non-resident as a local, which is the defect the "
                f"out-of-state column exists to fix")

    # And residency must actually change the answer where the data supports it.
    if resolve("CA", True) == resolve("CA", False):
        problems.append(
            "  the in_district flag changes nothing for California, where the "
            "published gap is nearly eightfold, so the flag is not reaching "
            "the lookup")
    return problems


def main() -> int:
    ns = load_app_namespace()
    problems = (check_coverage(ns) + check_keying(ns)
                + check_values(ns) + check_fallback(ns)
                + check_residency(ns))

    table = ns["COMMUNITY_COLLEGE_COST_BY_STATE"]
    if problems:
        print(f"community-college cost table: {len(problems)} problem(s)\n")
        for p in problems:
            print(p + "\n")
        print("  These fifty numbers are hand-typed and nothing regenerates\n"
              "  them. On a 2+2 path the whole modelled benefit is the tuition\n"
              "  avoided during the community-college years, so a wrong entry\n"
              "  moves every scenario in that state and looks entirely normal.")
        return 1

    lo = min(table, key=table.get)
    hi = max(table, key=table.get)
    print(f"community-college cost table OK: {len(table)} states, "
          f"${table[lo]:,} ({lo}) to ${table[hi]:,} ({hi}), "
          f"national ${ns['COMMUNITY_COLLEGE_COA_DEFAULT']:,}")
    print("  in-district tuition and fees, NOT a cost of attendance")
    return 0


if __name__ == "__main__":
    sys.exit(main())

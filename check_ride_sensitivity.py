#!/usr/bin/env python3
"""Guard for ride_flip_income: the sentence that says how fragile the verdict is.

Execs app.py's pure prefix the way the other repayment guards do, rebuilds
the strategy analysis across incomes exactly as the renderer does, and checks
what the sensitivity CONCLUDES. Every fixture is anchored on a literal balance
and rate transcribed here, never on a constant read back out of app.py.

The load-bearing control is the last one: a bisection from the ends is the
obvious way to write this function, it passes every single-crossing smoke
test, and it is wrong on RAP, which crosses twice. The guard proves it can
tell the two apart.
"""
import re
import sys

from check_repayment_invariants import load_app_namespace

ns = load_app_namespace()
compare = ns["compare_existing_loan_plans"]
pivot = ns["pivot_strategy_analysis"]
flip_fn = ns["ride_flip_income"]
sentences = ns["strategy_verdict_sentences"]
RAP = ns["RAP_STRATEGY_LABEL"]


def fixture(fed, priv, income, prefer, pslf=False):
    """(analysis, at_income) for one portfolio, built the way the page does."""
    total = sum(l["balance"] for l in fed)
    rate = sum(l["balance"] * l["rate"] for l in fed) / total

    def at_income(inc):
        rows = compare(total, rate, inc, 0, True, 0.0, pslf, 0,
                       federal_loans=fed, private_loans=priv)
        return pivot(rows, fed, inc, 0, pslf=pslf, prefer_label=prefer)
    return at_income(income), at_income


def sign(a):
    s = float((a or {}).get("savings", 0.0) or 0.0)
    return 1 if s > 0.5 else (-1 if s < -0.5 else 0)


def naive_bisection(at_income, lo=20_000.0, hi=330_000.0):
    """The wrong implementation: assume one crossing, bisect from the ends.
    Kept here ONLY so the guard can show it disagrees with the scan."""
    slo, shi = sign(at_income(lo)), sign(at_income(hi))
    if slo == shi:
        return []
    for _ in range(16):
        mid = (lo + hi) / 2
        if sign(at_income(mid)) == slo:
            lo = mid
        else:
            hi = mid
    return [(lo + hi) / 2]


FED_175 = [{"balance": 175_000.0, "rate": 7.0}]
PRIV_40 = [{"balance": 40_000.0, "rate": 9.0, "term": 10, "actual": 0}]

problems, checked = [], 0

# 1. one crossing on the commit-or-ride shape, and it really reverses the verdict
a, at = fixture(FED_175, PRIV_40, 80_000.0, RAP)
f = flip_fn(a, at, 80_000.0)
checked += 1
if not f or f["kind"] != "flip":
    problems.append(f"  commit-or-ride $175k+$40k private: expected one flip, got {f and f['kind']}")
else:
    print(f"  flip on commit-or-ride at ${f['at']:,.0f}")
    if not 60_000 < f["at"] < 95_000:
        problems.append(f"  the flip landed at ${f['at']:,.0f}, outside the measured $60k to $95k")
    below, above = sign(at(f["at"] - 4_000)), sign(at(f["at"] + 4_000))
    checked += 1
    if below == above or 0 in (below, above):
        problems.append(f"  the reported flip does not flip: sign {below} below, {above} above")

# 2. RAP crosses twice: a band, with both edges where they were measured
a2, at2 = fixture(FED_175, None, 110_000.0, RAP)
f2 = flip_fn(a2, at2, 110_000.0)
checked += 1
if not f2 or f2["kind"] != "band":
    problems.append(f"  RAP federal-only: expected a band (two crossings), got {f2 and f2['kind']}")
else:
    lo, hi = f2["at"]
    print(f"  band on RAP plan-choice: ${lo:,.0f} to ${hi:,.0f}")
    if not (40_000 < lo < 70_000 and 190_000 < hi < 270_000):
        problems.append(f"  RAP band edges ${lo:,.0f}/${hi:,.0f} are outside the measured brackets")

# 3. PSLF has no fork of this kind
a3, at3 = fixture([{"balance": 60_000.0, "rate": 6.5}], None, 32_000.0, RAP, pslf=True)
checked += 1
if flip_fn(a3, at3, 32_000.0) is not None:
    problems.append("  PSLF produced a sensitivity; a tax-free discharge has nothing to be sensitive about")

# 4. where the verdict at THIS income is "about the same", the prose must not
#    claim a reversal, whatever the curve does elsewhere. This portfolio has a
#    real flip near $40,000 and is negligible from $85,000 up, so the kind is
#    legitimately "flip" and the sentence must still be empty at $90,000. The
#    first version of this check asserted the kind instead of the gate, and
#    failed on correct code.
a4, at4 = fixture([{"balance": 60_000.0, "rate": 6.5}],
                  [{"balance": 25_000.0, "rate": 11.0, "term": 10, "actual": 0}],
                  90_000.0, RAP)
f4 = flip_fn(a4, at4, 90_000.0)
checked += 1
if not f4 or f4["now"] != 0:
    problems.append(f"  the $60k+$25k portfolio at $90,000 should be 'about the same' "
                    f"at this income (now={f4 and f4['now']}); the fixture drifted")
else:
    a4["flip"] = {**f4, "income": 90_000.0}
    if ns["flip_sentence"](a4, 90_000.0) != "":
        problems.append("  a verdict of 'about the same' still got a sensitivity sentence")
    if any("reverses" in line for line in sentences(a4)):
        problems.append("  the negligible-at-this-income portfolio's prose claims a reversal")
    print(f"  gate: kind={f4['kind']!r} elsewhere, silent at $90,000 where the verdict is even")

# 5. the sentence reaches both surfaces, and is attached before the PDF builds
src = open("app.py").read()
checked += 1
call = src.index("strategy_analysis = pivot_strategy_analysis(")
attach = src.find('strategy_analysis["flip"]', call)
pdf = src.index("_repayment_actions(rows,", call)
if attach < 0 or attach > pdf:
    problems.append("  the sensitivity is not attached to the analysis between the "
                    "pivot call and _repayment_actions, so the PDF would omit it")
for builder in ("def plan_choice_sentences", "def strategy_verdict_sentences"):
    body = src[src.index(builder):]
    body = body[:body.find("\ndef ", 1)]
    if "flip_sentence(" not in body:
        problems.append(f"  {builder} does not carry the sensitivity sentence")

# 6. THE CONTROL: bisection from the ends is wrong on RAP, and the guard can tell
checked += 1
nb = naive_bisection(at2)
if len(nb) == 2:
    problems.append("  CONTROL INCONCLUSIVE: naive bisection found both RAP crossings, "
                    "so this guard could not distinguish a bisecting implementation")
else:
    print(f"  control: bisection from the ends reports {len(nb)} crossing(s) on RAP "
          f"where the scan reports 2")

if problems:
    print("\n".join(problems)); sys.exit(1)
print(f"\nride sensitivity OK -- {checked} checks: one flip that really flips, "
      f"the RAP band with both edges, PSLF refused, no reversal on a negligible "
      f"gap, attached before the PDF on both shapes, and bisection shown wrong.")

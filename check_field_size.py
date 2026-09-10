#!/usr/bin/env python3
"""How many people hold this job is a fact, and it stays a fact.

WHY THIS EXISTS. data_pipeline.py has always written `tot_emp` and app.py
never read it, so until 2026-09-09 an occupation with 220 people in it and
one with 4,305,810 were presented identically: same break-even, same
crossover age, nothing on the page telling them apart. The calculator will
state a break-even of about $723,100 for prosthodontists, of whom there are
870 in the United States, in exactly the shape it states one for registered
nurses.

TWO WAYS THIS GOES WRONG, and both are silent.

The first is that it stops being national. The metro and state overlays in
build_major_data replace the wage pair and the percentiles and nothing else,
on the stated principle that everything else is a property of the occupation
rather than of where it is done. Employment is NOT such a property. If an
overlay ever carried it, "national employment" would quietly become metro
employment, the number would shrink by an order of magnitude, and the
sentence beside it would still say national.

The second is that it stops being a fact. A count is information; a
threshold is a judgment. The moment this says "small", or "consider", or
sorts anything, it is telling a seventeen-year-old what to want, which is
what the AI module's steering did until it was deleted on the same day this
shipped.

WHAT IT ASSERTS:

  1. Every OEWS occupation carries a positive integer count, and the field
     is named for what it is.
  2. THE OVERLAYS CANNOT MOVE IT. Built for a metro city, every occupation's
     count equals the national one, including the occupations whose WAGES
     the overlay did change. That second half is the check: an assertion
     that only visited unchanged rows would pass on a broken overlay.
  3. The disclosure carries both figures, names no threshold, ranks nothing
     and advises nothing.
  4. It degrades to "" rather than raising for a curated entry, which is not
     an OEWS occupation and has no count.
  5. It reaches BOTH result branches and the PDF. A disclosure in one arm
     and not the other is an H2 confound, not a layout choice.
  6. IT MOVES NO MONEY, by AST: only renderers may read it.

Six negative controls, all run on every invocation.
"""

import ast
import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parent
SRC = REPO / "app.py"

FIELD = "national_employment"
# A count is information and a threshold is a judgment. None of these may
# appear in the rendered sentence.
BANNED = ["small", "large", "tiny", "niche", "crowded", "consider", "instead",
          "should", "risky", "safer", "avoid", "few jobs", "plenty"]
# Functions allowed to touch it. All render; none returns money.
READERS = {"load_bls_careers", "field_size_median", "field_size_counted",
           "field_size_disclosure",
           "render_scenario_panel", "_pdf_sources_section"}

# A metro this app ships with, used to prove the overlay cannot move the
# count. Named rather than derived: the point is to exercise a real overlay.
METRO_CITY = "San Francisco, CA"


def load():
    import analyze_model
    with redirect_stderr(io.StringIO()):
        return analyze_model.load_model_layer()


def readers_of(tree):
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        strings = {c.value for c in ast.walk(node)
                   if isinstance(c, ast.Constant) and isinstance(c.value, str)}
        if FIELD in strings or "field_size_disclosure" in names or "field_size_median" in names:
            found[node.name] = True
    return set(found)


def check(ns, src, tree, national=None, overlaid=None):
    problems = []
    md = national if national is not None else ns["MAJOR_DATA"]

    # 1. present and well formed
    counts = {t: v.get(FIELD) for t, v in md.items()}
    bad = {t: c for t, c in counts.items()
           if c is not None and not (isinstance(c, int) and c > 0)}
    if bad:
        problems.append(f"  {len(bad)} occupation(s) carry a non-positive or non-integer "
                        f"count, e.g. {list(bad.items())[:3]}. 0 is a real answer meaning "
                        f"nobody does the job and no occupation in the file has it, so a 0 "
                        f"here is a parse failure wearing a real value's clothes.")
    have = [c for c in counts.values() if c]
    if len(have) < 800:
        problems.append(f"  only {len(have)} occupations carry a count; the OEWS file has 825 "
                        f"with no nulls. A mostly-empty column renders a mostly-absent "
                        f"sentence, which looks like a design choice.")

    # 2. the overlay cannot move it, INCLUDING where it moved the wage
    if overlaid is not None:
        moved = [t for t in overlaid
                 if overlaid[t].get(FIELD) != md.get(t, {}).get(FIELD)]
        if moved:
            problems.append(f"  {len(moved)} occupation(s) change their count under the "
                            f"{METRO_CITY} overlay, e.g. {moved[:3]}. Employment is national "
                            f"and the sentence beside it says so.")
        wage_moved = [t for t in overlaid
                      if overlaid[t].get("median_salary") != md.get(t, {}).get("median_salary")]
        if not wage_moved:
            problems.append(f"  the {METRO_CITY} overlay changed no wage at all, so the check "
                            f"above never visited an overlaid row and proves nothing. This is "
                            f"the inconclusive-control failure, arriving inside a guard.")

    # 3. the sentence is a fact. Skipped when nothing carries a count, which
    # section 1 has already reported: a second cascade of failures from one
    # cause reads as four bugs.
    fn = ns.get("field_size_disclosure")
    sized = [t for t in md if md[t].get(FIELD)]
    pair = ((max(sized, key=lambda t: md[t][FIELD]),
             min(sized, key=lambda t: md[t][FIELD])) if sized else ())
    for title in pair:
        text = fn(title)
        if not text:
            problems.append(f"  {title!r} has a count of {md[title].get(FIELD)} and renders "
                            f"no sentence.")
            continue
        if f"{md[title][FIELD]:,}" not in text:
            problems.append(f"  the sentence for {title!r} does not carry its own count "
                            f"{md[title][FIELD]:,}.")
        if f"{ns['field_size_median']():,}" not in text:
            problems.append(f"  the sentence for {title!r} carries no median to compare "
                            f"against, so the count is a number with no scale.")
        hits = [w for w in BANNED if w in text.lower()]
        if hits:
            problems.append(f"  the sentence for {title!r} uses {hits}. A count is "
                            f"information and a judgment is advice. This module states two "
                            f"figures and stops, for the reason the AI steering was deleted.")

    # 4. degrades rather than raises
    for absent in ("Medicine", "Law", "not an occupation at all"):
        try:
            if fn(absent) != "":
                problems.append(f"  {absent!r} has no OEWS count and still renders a "
                                f"sentence.")
        except Exception as exc:                      # noqa: BLE001
            problems.append(f"  field_size_disclosure({absent!r}) raised {exc!r}; a curated "
                            f"entry must lose a sentence, never a page.")

    # 5. both arms and the report
    # TWO CALLS, ONE PER BRANCH. render_scenario_panel is Compare Mode only
    # (its two call sites are the A and B columns), so it does NOT cover the
    # single arm and the single branch needs its own. Getting this backwards
    # first deleted the single arm's copy and nothing failed, because the H2
    # coin flip had put the verifying run in the contrast arm: half of all
    # sessions would have shown the sentence and half would not, which is the
    # confound this rule exists to prevent. Force ?compare= when checking an
    # arm by hand.
    for surface, marker in {
        "the compare arm": 'field_size_disclosure(scenario["major"])',
        "the single arm": "field_size_disclosure(major)",
        "the PDF": "field_size_disclosure(name, for_pdf=True)",
    }.items():
        if marker not in src:
            problems.append(f"  {surface}: no longer renders the field size ({marker!r} not "
                            f"found). One arm and not the other is an H2 confound, and the "
                            f"arms are assigned by coin flip, so it hides in half of runs.")

    # 6. no money function reads it
    for name in sorted(readers_of(tree) - READERS):
        problems.append(f"  {name}() reads the employment count and is not an allowed "
                        f"reader. This is disclosure: it moves no salary, no loan and no "
                        f"premium.")
    return problems


def controls(ns, src, tree, national, overlaid):
    fired, fails = [], []

    def run(label, expect, **kw):
        got = check(**kw)
        if not any(expect in p for p in got):
            fails.append(f"control {label!r} did not fire (expected {expect!r}; got "
                         f"{got[:1] or 'nothing'})")
        else:
            fired.append(label)

    def with_ns(mutate):
        n = dict(ns)
        return mutate(n)

    zeroed = {t: {**v, FIELD: 0} for t, v in national.items()}
    run("a count of zero", "non-positive",
        ns=ns, src=src, tree=tree, national=zeroed, overlaid=None)

    metro_emp = {t: {**v, FIELD: 1234} for t, v in overlaid.items()}
    run("the overlay carrying metro employment", "change their count",
        ns=ns, src=src, tree=tree, national=national, overlaid=metro_emp)

    run("an overlay that changed no wage", "never visited an overlaid row",
        ns=ns, src=src, tree=tree, national=national,
        overlaid={t: dict(national[t]) for t in list(national)[:5]})

    def judge(n):
        base = n["field_size_disclosure"]
        n["field_size_disclosure"] = lambda t, for_pdf=False: (
            (base(t, for_pdf) + " That is a small field.") if base(t, for_pdf) else "")
        return n
    run("the sentence calling a field small", "uses ['small']",
        ns=with_ns(judge), src=src, tree=tree, national=national, overlaid=None)

    def no_median(n):
        base = n["field_size_disclosure"]
        n["field_size_disclosure"] = lambda t, for_pdf=False: (
            base(t, for_pdf).split(", against")[0] if base(t, for_pdf) else "")
        return n
    run("the median dropped from the sentence", "no median to compare",
        ns=with_ns(no_median), src=src, tree=tree, national=national, overlaid=None)

    run("the compare arm stops rendering it", "the compare arm:",
        ns=ns, src=src.replace('field_size_disclosure(scenario["major"])', "_gone(x)"),
        tree=tree, national=national, overlaid=None)
    run("the single arm stops rendering it", "the single arm:",
        ns=ns, src=src.replace("field_size_disclosure(major)", "_gone(m)"),
        tree=tree, national=national, overlaid=None)
    return fails, fired


def main():
    src = SRC.read_text()
    tree = ast.parse(src)
    ns = load()
    national = ns["MAJOR_DATA"]
    with redirect_stderr(io.StringIO()):
        overlaid = ns["build_major_data"](ns["CAREERS_CSV_PATH_NATIONAL"],
                                          ns["DATASET_MODE_CAREER"], METRO_CITY)
    problems = check(ns, src, tree, national=national, overlaid=overlaid)
    fails, fired = controls(ns, src, tree, national, overlaid)
    if problems or fails:
        print("check_field_size FAILED\n")
        for line in problems + [f"  {f}" for f in fails]:
            print(line)
        return 1
    counts = sorted(v[FIELD] for v in national.values() if v.get(FIELD))
    print(f"check_field_size OK: {len(counts)} occupations carry a national count "
          f"({counts[0]:,} to {counts[-1]:,}); the {METRO_CITY} overlay moves wages and not "
          f"counts; the sentence states two figures, ranks nothing and reaches both arms and "
          f"the report. {len(fired)} negative controls fired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

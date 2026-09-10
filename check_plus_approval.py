#!/usr/bin/env python3
"""The Parent PLUS offer is stated as conditional, and stays stated in full.

WHY THIS EXISTS. Until 2026-09-10 nothing in this app said that the Direct
PLUS figure on an aid letter is applied for after the letter and can be
refused. The whole cap-and-gap split treats the $65,000 as capacity a family
HAS, and the app warns at length about the credit check on PRIVATE money
while never mentioning the one on the federal loan above it. The gap was
found by reading a lender's own calculator, which says it plainly.

WHAT DECAYS. A note like this decays in one direction: somebody trims it to
the alarming half and it becomes a reason to expect refusal, or to the
reassuring half and it stops warning. 34 CFR 685.200(c)(2)(viii) has three
parts that must travel together, and this asserts all three:

  the test        (B), two specific lookups rather than a score, and income
                  is not one of them
  the ways back   (A)(2) and (A)(3), an endorser without an adverse history
                  or documented extenuating circumstances. "You will be
                  refused" would be false
  no history      (F), the absence of a credit history is NOT adverse credit
                  and is not grounds for refusal, which runs OPPOSITE to
                  private lending

Plus the fallout, which is the part a family acts on: a refusal raises the
student's own limit by $26,000 across four years rather than simply shrinking
the budget.

Every expectation below is TRANSCRIBED FROM THE REGULATION, never read back
off the constant under test.

Five negative controls, all run on every invocation.
"""

import ast
import io
import re
import sys
from contextlib import redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parent
SRC = REPO / "app.py"

# From 34 CFR 685.200(c)(2)(viii), read 2026-09-10 through the eCFR versioner.
MUST_CARRY = {
    "the test": ["$2,085", "90 days late", "collection", "charged off", "two years",
                 "bankruptcy", "foreclosure", "tax lien", "wage garnishment", "five"],
    "income is not part of it": ["Income is not tested"],
    "the ways back": ["endorser", "extenuating circumstances"],
    "no credit history is not adverse": ["no credit history at all does not count"],
    "what a refusal costs": ["$26,000"],
    "the citation": ["34 CFR 685.200(c)"],
}
# The regulation's own threshold, which (C) and (D) have the Secretary
# adjusting for CPI-U. Quoted, never computed with.
THRESHOLD = "$2,085"
SURFACES = {
    "the screen": 'st.info(PARENT_PLUS_APPROVAL_NOTE',
    "the report": "out.append(Paragraph(xml_escape(PARENT_PLUS_APPROVAL_NOTE",
}
READERS = {"render_financing_note", "_pdf_financing_flowables"}


def load():
    src = SRC.read_text()
    ns = {"__name__": "plus_approval"}
    with redirect_stderr(io.StringIO()):
        exec(compile(src[:src.index("# 3. PAGE CONFIG")], "app.py", "exec"), ns)
    return ns, src, ast.parse(src)


def readers_of(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(getattr(n, "id", "") == "PARENT_PLUS_APPROVAL_NOTE"
                   for n in ast.walk(node)):
                out.add(node.name)
    return out


def check(ns, src, tree):
    problems = []
    note = ns.get("PARENT_PLUS_APPROVAL_NOTE", "")
    if not note:
        return ["  PARENT_PLUS_APPROVAL_NOTE is missing entirely."]

    for half, needles in MUST_CARRY.items():
        missing = [n for n in needles if n not in note]
        if missing:
            problems.append(
                f"  the note has lost {half}: {missing} absent. All of it or none: the "
                f"regulation's three parts travel together, and half of them is either a "
                f"reason to expect refusal or a reason to stop reading.")

    # The threshold is quoted, not computed with. An arithmetic use would go
    # stale the first time the Secretary publishes an adjustment.
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and side.value == 2085:
                    problems.append("  2085 is used in arithmetic somewhere. It is the "
                                    "regulation's threshold and (C) and (D) have the "
                                    "Secretary adjusting it for inflation, so it may be "
                                    "quoted and never computed with.")

    for surface, marker in SURFACES.items():
        if marker not in src:
            problems.append(f"  {surface} no longer renders the note ({marker!r} not found). "
                            f"The report is the copy that survives the session, and the "
                            f"screen is the one read beside the letter.")

    # GATED ON THE PLUS TRANCHE, NOT ON THE GAP. An independent student has no
    # Parent PLUS at all, and warning them about approval for a loan they
    # cannot take is the error the "$0 Direct PLUS" line already avoids.
    for surface, marker in SURFACES.items():
        i = src.find(marker)
        window = src[max(0, i - 400):i]
        if 'financing.get("plus_principal"' not in window:
            problems.append(f"  {surface} does not gate the note on plus_principal. Gated on "
                            f"the gap instead, it fires for an independent student who has "
                            f"no Parent PLUS at all.")

    for name in sorted(readers_of(tree) - READERS):
        problems.append(f"  {name}() reads the note and is not an allowed renderer. This is "
                        f"disclosure: it moves no loan, no split and no premium.")
    return problems


def controls(ns, src, tree):
    fired, fails = [], []

    def run(label, expect, ns2=None, src2=None):
        if src2 is not None and src2 == src:
            fails.append(f"control {label!r} did not apply: the source is unchanged")
            return
        got = check(ns2 or ns, src2 or src, ast.parse(src2) if src2 else tree)
        if any(expect in p for p in got):
            fired.append(label)
        else:
            fails.append(f"control {label!r} did not fire (wanted {expect!r}; got "
                         f"{got[:1] or 'nothing'})")

    note = ns["PARENT_PLUS_APPROVAL_NOTE"]
    run("the ways back trimmed out", "lost the ways back",
        ns2={**ns, "PARENT_PLUS_APPROVAL_NOTE":
             note.replace("A refusal can be answered with an endorser who has "
                          "no adverse history or with documented extenuating circumstances, "
                          "and having ", "Having ")})
    run("the no-history clause trimmed out", "lost no credit history is not adverse",
        ns2={**ns, "PARENT_PLUS_APPROVAL_NOTE":
             note.replace("no credit history at all does not count", "it does not count")})
    run("the $26,000 fallout dropped", "lost what a refusal costs",
        ns2={**ns, "PARENT_PLUS_APPROVAL_NOTE": note.replace("$26,000", "more")})
    run("the report stops printing it", "the report no longer renders",
        src2=src.replace("out.append(Paragraph(xml_escape(PARENT_PLUS_APPROVAL_NOTE",
                         "out.append(Paragraph(xml_escape(_gone", 1))
    run("the threshold used in arithmetic", "used in arithmetic",
        src2=src.replace("PARENT_PLUS_LIMIT_EFFECTIVE_YEAR = 2026",
                         "PARENT_PLUS_LIMIT_EFFECTIVE_YEAR = 2026\n_x = 2085 * 2", 1))
    return fails, fired


def main():
    ns, src, tree = load()
    problems = check(ns, src, tree)
    fails, fired = controls(ns, src, tree)
    if problems or fails:
        print("check_plus_approval FAILED\n")
        for line in problems + [f"  {f}" for f in fails]:
            print(line)
        return 1
    print(f"check_plus_approval OK: the note carries all {len(MUST_CARRY)} parts of "
          f"34 CFR 685.200(c), reaches the screen and the report, is gated on the PLUS "
          f"tranche rather than the gap, and {THRESHOLD} is quoted and never computed with. "
          f"{len(fired)} negative controls fired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

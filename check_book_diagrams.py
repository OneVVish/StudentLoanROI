#!/usr/bin/env python3
"""Every book diagram agrees with the chapter that cites it.

    python3 check_book_diagrams.py

WHY THIS IS A SEPARATE QUESTION FROM check_book_figures.py. That guard proves
the book's money still comes out of the tool. This one proves the PICTURES
agree with the prose beside them. A figure and its chapter are both drawn from
app.py, so neither can drift from the MODEL, and that is exactly why nothing
noticed they could drift from EACH OTHER: re-run a fixture, move the chapter,
leave the figure, and both halves are individually correct while the page
contradicts itself.

It has a specific bug behind it. brand/book_figures.py's three-roads figure
was built on 2026-09-10 and its fast road came out $35,601 where chapter 7
says ~$34,100, because the chapter pays from DISBURSEMENT and the figure was
paying from ENTRY. A person caught that by reading two numbers side by side.
Nothing would have caught it twice.

COVERAGE IS DERIVED, NOT LISTED. Every figure in book_figures.FIGURES must
either register a claim or appear in NO_CLAIM with a reason, so a new figure
fails this guard until somebody decides which it is.

ONE ROUNDING RULE, NOT TWO. The acceptance test is check_book_figures.py's:
a citation is right when it is within half its own last significant place of
the computed value. This file parses percentages and "N million" as well as
money, so it carries its own parser, and check_parser_agrees asserts that
parser returns exactly what check_book_figures returns on money strings. Two
rounding rules on one manuscript is how a figure and a fixture come to
disagree about what "close enough" means.

IT SKIPS LOUDLY WHEN marketing/ IS ABSENT, which is every clone: the book is
in the gitignored mirror. Reporting OK over zero chapters is worse than no
check, so it says so and exits 0.
"""
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
BOOK = REPO / "marketing" / "book"

NUMBER = re.compile(r"-?[\d,]*\.?\d+")


def cited(text):
    """The value a citation states, and the size of its last place.

    Handles the four shapes the chapters use: "~$114,400", "$5,500", "30%"
    and "54.7 million". A trailing "million" scales the value but NOT the
    step, because "54.7 million" is quoted to one decimal of a million and
    its last place is 0.1 there, not 100,000 in units.
    """
    m = NUMBER.search(text)
    if not m:
        raise ValueError(f"no number in {text!r}")
    raw = m.group(0).replace(",", "")
    value = float(raw)
    step = 10 ** -len(raw.split(".")[1]) if "." in raw else 10 ** (
        len(raw) - len(raw.rstrip("0")))
    return value, float(step)


def check_parser_agrees():
    """This file's parser and check_book_figures' agree about money.

    Not a stylistic tidy: if the two disagree about a citation's last place
    they disagree about what "close enough" means, and the manuscript then
    has two rounding rules with nothing saying which applies where.
    """
    problems = []
    try:
        sys.path.insert(0, str(REPO))
        import check_book_figures as cbf
    except Exception as exc:                                   # noqa: BLE001
        return [f"  could not import check_book_figures ({exc}); the shared "
                f"rounding rule is unverified"]
    for cite in ("~$114,400", "$5,500", "~$7,000", "$4,000", "~$826", "$27,000"):
        mine_v, mine_s = cited(cite)
        theirs_v, theirs_s = cbf.money_value(cite), cbf.money_step(cite)
        if (mine_v, mine_s) != (theirs_v, theirs_s):
            problems.append(
                f"  {cite}: this file reads ({mine_v}, {mine_s}) and "
                f"check_book_figures reads ({theirs_v}, {theirs_s}). Two "
                f"rounding rules on one manuscript.")
    return problems


def run_figures():
    """Draw every figure into a scratch directory and collect its claims."""
    import contextlib, io
    sys.path.insert(0, str(REPO / "brand"))
    import book_figures as bf
    bf.OUT_DIR = Path(tempfile.mkdtemp())
    with contextlib.redirect_stdout(io.StringIO()):
        ns = bf.load_app()
        for name in sorted(bf.FIGURES):
            bf.FIGURES[name](ns)
    return bf


def check_claims(bf, chapters) -> list:
    problems = []
    for name, spec in sorted(bf.CLAIMS.items()):
        text = chapters.get(spec["chapter"])
        if text is None:
            problems.append(f"  {name}: names chapter {spec['chapter']!r}, "
                            f"which is not a file in marketing/book/")
            continue
        for cite, got in spec["cites"].items():
            if cite not in text:
                problems.append(
                    f"  {name}: {spec['chapter']} does not contain {cite!r}. "
                    f"Either the chapter stopped quoting this figure, or it "
                    f"was reworded and the figure's claim is stale.")
                continue
            want, step = cited(cite)
            # A STATUTORY FIGURE IS EXACT. "$5,500" reads as quoted to the
            # nearest hundred, so the half-place rule accepts $5,506 against
            # it: right for an estimate, wrong for a ceiling written into
            # law. A negative control caught exactly that.
            tol = 0.005 if cite in spec.get("exact", ()) else step / 2
            if abs(float(got) - want) > tol:
                kind = ("a statutory figure" if cite in spec.get("exact", ())
                        else f"half of its own last place ({step / 2:,.2f})")
                problems.append(
                    f"  {name}: the figure draws {got:,.2f} and "
                    f"{spec['chapter']} says {cite}, which is more than "
                    f"{kind} away. The picture and the prose on the same "
                    f"page disagree.")
    return problems


def check_coverage(bf) -> list:
    """Every figure is claimed or excused, and never both."""
    problems = []
    claimed, excused = set(bf.CLAIMS), set(bf.NO_CLAIM)
    for name in sorted(bf.FIGURES):
        if name not in claimed and name not in excused:
            problems.append(
                f"  {name}: draws a figure and neither claims anything of a "
                f"chapter nor appears in NO_CLAIM. Decide which: a figure "
                f"whose numbers nothing checks is the case this guard exists "
                f"for.")
    for name in sorted(claimed & excused):
        problems.append(f"  {name}: is in CLAIMS and in NO_CLAIM at once.")
    for name in sorted(excused - set(bf.FIGURES)):
        problems.append(f"  {name}: excused in NO_CLAIM and no longer drawn.")
    return problems


def controls(bf, chapters) -> list:
    """Break it four ways on purpose, and fail if any of them passes.

    They run on EVERY invocation rather than behind a flag, because a guard
    nobody has watched fail is a guard nobody knows the shape of. Each one
    works on the collected claims rather than by re-rendering, so the cost is
    nothing and each control tests the CHECK rather than the drawing.

    The first is the bug this file was written for, reproduced: the
    three-roads figure paid its fast road from ENTRY rather than from
    DISBURSEMENT and came out $35,601 where chapter 7 says ~$34,100.
    """
    import copy
    dead = []

    real = copy.deepcopy(bf.CLAIMS)
    hurt = copy.deepcopy(real)
    hurt["three-roads"]["cites"]["~$34,100"] = 35_601.0
    bf.CLAIMS = hurt
    if not check_claims(bf, chapters):
        dead.append("  [the original bug] the fast road at $35,601 against a "
                    "chapter saying ~$34,100 was accepted")

    hurt = copy.deepcopy(real)
    hurt["in-state"]["cites"]["~$999,000"] = hurt["in-state"]["cites"].pop("~$7,000")
    bf.CLAIMS = hurt
    if not check_claims(bf, chapters):
        dead.append("  [absent citation] a claim on a string the chapter does "
                    "not contain was accepted")

    hurt = copy.deepcopy(real)
    hurt["cap-ladder"]["cites"]["$5,500"] = 5_506.0
    bf.CLAIMS = hurt
    if not check_claims(bf, chapters):
        dead.append("  [a statutory figure] $5,506 against a statutory "
                    "$5,500 was accepted; a ceiling written into law is not "
                    "a figure rounded to the nearest hundred")

    bf.CLAIMS = real
    keep_no = dict(bf.NO_CLAIM)
    bf.NO_CLAIM = {k: v for k, v in keep_no.items() if k != "households"}
    if not check_coverage(bf):
        dead.append("  [coverage] a figure claiming nothing and excused "
                    "nowhere was accepted")
    bf.NO_CLAIM = keep_no
    return dead


def main():
    if not BOOK.exists():
        print("check_book_diagrams SKIPPED: marketing/book is not in this "
              "clone, so there are no chapters to check the figures against. "
              "This is expected on CI and on any machine but the author's.")
        return 0
    chapters = {p.name.split("-")[0]: p.read_text() for p in BOOK.glob("ch*.md")}
    bf = run_figures()
    problems = (check_parser_agrees() + check_coverage(bf)
                + check_claims(bf, chapters))
    dead = controls(bf, chapters)
    if dead:
        problems += ["  A NEGATIVE CONTROL DID NOT FIRE, so the checks above "
                     "prove less than they appear to:"] + dead
    if problems:
        print("check_book_diagrams FAILED:")
        print("\n".join(problems))
        return 1
    n = sum(len(s["cites"]) for s in bf.CLAIMS.values())
    print(f"check_book_diagrams OK: {len(bf.FIGURES)} figures, "
          f"{len(bf.CLAIMS)} of them claiming {n} figures across "
          f"{len({s['chapter'] for s in bf.CLAIMS.values()})} chapters, "
          f"{len(bf.NO_CLAIM)} excused with a reason. "
          f"4 negative controls fired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

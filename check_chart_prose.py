#!/usr/bin/env python3
"""Guard: every word a chart draws follows this site's prose rules.

    python3 check_chart_prose.py        (exit 1 on a violation)

The guides and app.py were standardised on American English and on no dash
punctuation, and check_reel.py already holds the Instagram reels to both -- a
burned-in caption is a rendered visitor-facing string and is held to app.py's
rules. THE CHARTS WERE NEVER CHECKED, and they are the most widely seen surface
here: an infographic gets posted, screenshotted and reposted with none of the
site around it.

Reported by a reader of the drafts, not by any check: "programmes" was on a
published chart, and the sweep that followed found the repo half-converted --
three chart scripts already said "Modeled" while two said "modelled", which is
the state a codebase reaches when the rule lives in someone's head.

WHAT IT READS, AND WHAT IT DELIBERATELY DOES NOT. Only string constants that
reach a text-drawing call: fig.text, ax.text, annotate, the tick labels, the
legend. Docstrings and comments are NOT visitor-facing and keep whatever
spelling they have -- CLAUDE.md says so explicitly, and this file's own prose
would fail its own check otherwise. Dictionary keys are excluded too: `x["colour"]`
inside a text call is an identifier, and renaming it would be a refactor wearing
a copy edit's clothes.

THE EM DASH RULE IS NOT ONLY STYLE. The audience is 17 to 21, and a sentence
with two nested dash asides makes a reader hold a clause open while parsing an
interruption. On a chart, where the caption competes with the picture, that costs
more than it does in prose.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHARTS = ROOT / "marketing"

# The calls that put a string in front of a reader.
DRAWING_CALLS = {"text", "annotate", "set_yticklabels", "set_xticklabels",
                 "set_title", "set_xlabel", "set_ylabel", "legend", "suptitle"}

# British spellings, with the American form to use. Hand-listed rather than
# derived: a general -ise/-ize rule would rewrite "advertise" and "surprise",
# and a general -our rule would rewrite "four".
BRITISH = {
    "programme": "program", "programmes": "programs",
    "modelled": "modeled", "modelling": "modeling",
    "cancelled": "canceled", "labelled": "labeled",
    "licence": "license", "defence": "defense", "offence": "offense",
    "standardised": "standardized", "organised": "organized",
    "recognised": "recognized", "analysed": "analyzed",
    "amortised": "amortized", "annualised": "annualized",
    "capitalised": "capitalized", "specialised": "specialized",
    "colour": "color", "colours": "colors", "coloured": "colored",
    "behaviour": "behavior", "neighbour": "neighbor", "favour": "favor",
    "centre": "center", "towards": "toward", "whilst": "while",
    "practise": "practice", "enrolment": "enrollment",
    "judgement": "judgment", "ageing": "aging",
}

DASHES = {"—": "em dash", "–": "en dash"}


def drawn_strings(path: Path):
    """(line, text) for every constant that reaches a text-drawing call.

    Subscript keys are skipped: they are identifiers that happen to sit inside
    the call, not words anybody reads.
    """
    tree = ast.parse(path.read_text())
    keys = {id(s) for node in ast.walk(tree) if isinstance(node, ast.Subscript)
            for s in ast.walk(node.slice)
            if isinstance(s, ast.Constant) and isinstance(s.value, str)}
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if name not in DRAWING_CALLS:
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                    and id(sub) not in keys):
                out.append((sub.lineno, sub.value))
    return out


def check(path: Path) -> list:
    problems = []
    for line, text in drawn_strings(path):
        low = text.lower()
        for word, better in BRITISH.items():
            if re.search(rf"\b{word}\b", low):
                problems.append(
                    f"  {path.name}:{line} draws {word!r}; this site writes "
                    f"{better!r}. Charts are the most reposted surface here and "
                    f"carry none of the site around them.")
        for glyph, label in DASHES.items():
            if glyph in text:
                problems.append(
                    f"  {path.name}:{line} draws an {label}. Use a period, a "
                    f"comma or a colon: the audience is 17 to 21, and a caption "
                    f"competing with a picture cannot afford a nested aside.")
    return problems


def main() -> int:
    if not CHARTS.is_dir():
        # marketing/ is gitignored, so a clone genuinely has nothing to read.
        # Said out loud rather than passing quietly: a guard that reports OK on
        # zero files is the shape this repo already records as worse than none.
        print("chart prose: SKIPPED, marketing/ is not present in this checkout")
        return 0

    scripts = sorted(p for p in CHARTS.glob("*.py") if p.name != "reel.py")
    problems, strings = [], 0
    for path in scripts:
        try:
            found = drawn_strings(path)
        except SyntaxError as err:
            problems.append(f"  {path.name} does not parse: {err}")
            continue
        strings += len(found)
        problems.extend(check(path))

    if problems:
        print(f"chart prose: {len(problems)} problem(s)\n")
        for p in problems:
            print(p + "\n")
        print("  Only strings reaching a drawing call are read. Docstrings,\n"
              "  comments and dictionary keys are not visitor-facing and are\n"
              "  deliberately left alone.")
        return 1

    print(f"chart prose OK: {strings} drawn string(s) across {len(scripts)} "
          f"chart script(s), American English, no dash punctuation")
    return 0


if __name__ == "__main__":
    sys.exit(main())

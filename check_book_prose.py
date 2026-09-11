#!/usr/bin/env python3
"""The book's prose keeps the house rules, and something checks.

    python3 check_book_prose.py

WHY THIS EXISTS. Every dollar figure in the manuscript is guarded twice, by
check_book_figures against the tool and by check_book_diagrams against the
pictures. The PROSE was guarded by nobody. On 2026-09-10 a hand sweep found 74
double hedges across 13 files, "about ~$622,000" and its like, where the tilde
is already the hedge and the adverb doubles it; chapter 11 alone held 14 and
its own editorial review had not flagged one of them. The same sweep turned up
a British spelling in families.md. A rule that lives only in content/README.md
and in whoever last read it is a rule that comes back.

WHAT IT READS, AND WHAT IT DOES NOT.

  * ch*.md and families.md. Those are the printed book and the fixture file
    the chapters quote, and every word in them is ours.
  * NOT sources.md, which is quotations from outside. A source may spell
    "programme", may use an em dash, and must be recorded as it was written.
    Correcting a quotation to house style is a worse fault than the style
    violation, so the file is out of scope rather than exempted line by line.
  * NOT figures.md, which is a working manifest that quotes chart decks and
    shell invocations verbatim.

THREE THINGS THAT WOULD BE FALSE POSITIVES, and each is real in this
manuscript rather than hypothetical: 68 markdown table delimiter rows made of
hyphens, 14 inline code spans holding `--balance` and its like, and image
paths and URLs full of hyphens. All are stripped before a single rule runs. A
guard that flags correct prose gets switched off, which is this repository's
own finding about check_chart_prose.

SKIPS LOUDLY when marketing/ is absent, that directory being gitignored, and
exits 0. Reporting OK over zero files is worse than no check at all.
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent
BOOK = REPO / "marketing" / "book"

# Hand-written, not derived. A general -ise rule rewrites "advertise" and a
# general -our rule rewrites "four"; check_chart_prose records the same.
BRITISH = ("programme", "programmes", "modelled", "modelling", "licence",
           "cancelled", "labelled", "amortised", "annualised", "capitalised",
           "greyed", "neighbour", "standardised", "unrecognised", "colour",
           "colours", "analyse", "analysed", "centre", "whilst", "towards",
           "organise", "organised", "favourite", "honour", "behaviour",
           "maths", "defence", "travelled", "enrolment")

# NO STATUTORY-FIGURE CHECK, AND THAT IS A FINDING RATHER THAN AN OMISSION.
# The rule is real: a federal limit is exact and "about $65,000" is false. It
# is not mechanically checkable, because the same amount is a cap in one
# sentence and a rounded median in another. A first version of this guard
# flagged six such figures and every one was correct prose: ~$5,500 of tuition
# and fees, ~$6,500 of interest against the ten-year schedule, ~$65,000 of a
# graduate's starting salary, ~$31,000 at the top of a wage range. Only the
# sentence around the number knows which it is. A check that fires on correct
# prose gets the whole guard switched off, which is this repository's own
# finding about check_chart_prose, so the rule stays with the reader.

# The published rate is about one per 1,400 words and the manuscript currently
# runs at zero. It is a register, not a ban, so this is a ceiling rather than
# an assertion of zero.
CONTRACTIONS_PER_WORDS = 1400


def readable(text: str) -> str:
    """The prose, with everything that is not prose removed."""
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)      # front matter
    text = re.sub(r"```.*?```", " ", text, flags=re.S)            # fenced code
    text = re.sub(r"`[^`]*`", " ", text)                          # inline code
    text = re.sub(r"^\|[\s|:\-]+\|\s*$", " ", text, flags=re.M)   # table rules
    text = re.sub(r"(?m)^\s*[-*+]\s", " ", text)                # list markers
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)        # links, alt kept
    text = re.sub(r"https?://\S+", " ", text)
    return text


CONTRACTION = re.compile(r"\b\w+['’](s|t|re|ve|ll|d|m)\b", re.I)


def check(paths):
    problems = []
    total_words = 0
    contractions = []
    for p in paths:
        t = readable(p.read_text())
        total_words += len(t.split())

        for m in re.finditer(r"[—–]|(?<=\s)-(?=\s)", t):
            problems.append(f"{p.name}: dash punctuation at {t[max(0,m.start()-45):m.start()+25]!r}")
        for w in BRITISH:
            for m in re.finditer(rf"\b{w}\b", t, re.I):
                problems.append(f"{p.name}: British spelling {m.group()!r}")
        for m in re.finditer(r"\bpercent\b", t):
            problems.append(f"{p.name}: 'percent' spelled out; the book uses the % sign")
        for m in re.finditer(r"\b(about|roughly|around|approximately|some)\s+~\$", t, re.I):
            problems.append(f"{p.name}: double hedge {m.group()!r}; the tilde is the hedge")
        # A PARAGRAPH or a sentence, never a wrapped line: families.md is hard
        # wrapped and four figures landed at the start of a continuation line.
        for m in re.finditer(r"(\n\n~\$|(?<=\.\s)~\$|\A~\$)", t):
            problems.append(f"{p.name}: a sentence opens on a tilde; rephrase rather than drop it")
        for m in CONTRACTION.finditer(t):
            if not m.group().lower().endswith("'s") and not m.group().endswith("’s"):
                contractions.append(f"{p.name}: {m.group()!r}")

    allowed = max(1, total_words // CONTRACTIONS_PER_WORDS)
    if len(contractions) > allowed:
        problems.append(f"contractions: {len(contractions)} in {total_words:,} words, "
                        f"above the ceiling of {allowed} (about one per "
                        f"{CONTRACTIONS_PER_WORDS} words). " + "; ".join(contractions[:6]))
    return problems, total_words, len(contractions)


def negative_controls(paths):
    """Break the prose deliberately and confirm each rule fires."""
    sample = paths[0].read_text()
    fired = []
    cases = [
        ("em dash", sample + "\nA sentence with an em dash — like this one.\n"),
        ("British spelling", sample + "\nThe programme was modelled carefully.\n"),
        ("percent", sample + "\nAbout 40 percent of borrowers.\n"),
        ("double hedge", sample + "\nIt costs about ~$12,000 a year.\n"),
        ("tilde-initial", sample + "\n~$3,000 of that is tuition.\n"),
        ("contractions", sample + ("\nIt isn't and they're and we'll and I've. " * 200)),
    ]
    tmp = paths[0].parent / "_prose_control.md"
    for label, text in cases:
        tmp.write_text(text)
        try:
            probs, _, _ = check([tmp])
        finally:
            tmp.unlink()
        if probs:
            fired.append(label)
        else:
            print(f"  NEGATIVE CONTROL DID NOT FIRE: {label}")
    return fired


def main() -> int:
    if not BOOK.exists():
        print("check_book_prose SKIPPED: marketing/ is gitignored and absent "
              "on this checkout. Nothing was read and nothing is claimed.")
        return 0
    paths = sorted(BOOK.glob("ch*.md"))
    fixtures = BOOK / "families.md"
    if fixtures.exists():
        paths.append(fixtures)
    if not paths:
        print("check_book_prose FAILED: no chapters found under marketing/book/")
        return 1

    problems, words, contractions = check(paths)
    fired = negative_controls(paths)
    if len(fired) != 6:
        print(f"check_book_prose FAILED: only {len(fired)} of 6 negative controls fired")
        return 1
    if problems:
        print(f"check_book_prose FAILED: {len(problems)} problem(s)\n")
        for p in problems[:40]:
            print(f"  {p}")
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
        return 1
    print(f"check_book_prose OK: {len(paths)} file(s), {words:,} words of prose. "
          f"American English, no dash punctuation, the % sign, no double hedge, "
          f"no sentence opening on a tilde, {contractions} contraction(s) against a "
          f"ceiling of {max(1, words // CONTRACTIONS_PER_WORDS)}. "
          f"{len(fired)} negative controls fired.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

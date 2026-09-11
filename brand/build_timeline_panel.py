#!/usr/bin/env python3
"""Three moments in one life, as three placeable panels.

    python3 brand/build_timeline_panel.py --list
    python3 brand/build_timeline_panel.py --panel applying --dry-run
    python3 brand/build_timeline_panel.py --panel applying
    python3 brand/build_timeline_panel.py --self-test

One file per MOMENT rather than one page per household, so a chapter takes the
panel its argument is about: the applying panel suits anything in Parts I and
II, the graduation panel the chapters that price an outcome, and the ten-years
panel the repayment chapters. Nothing here is keyed to a household, which is
what makes them reusable.

THE LABEL IS DRAWN HERE, NEVER PROMPTED. klein4b's lettering is unreliable and
six props had to be swapped out of the chapter openers for exactly that reason
(see `build_book_opener.py`). The illustration stays wordless and this script
sets the label in a real font, so the panel is self-describing wherever it is
placed and the words are ours.

IT SHARES THE OPENERS' STYLE CONTRACT by importing it rather than restating it.
Same book, same illustrator: move `OPENER_STYLE` and these move with it.

WHAT A PANEL MAY NOT SAY. These are three moments, not three verdicts.
`content/README.md` forbids a verdict on an outcome and the book prices trades
rather than recommending them, so no panel shows money, a reward, a hardship or
a symbol of either. A person at a kitchen table and the same person at their own
table ten years later is a moment; either one beside a pile of money is a claim
the book does not make. The scenes are written to that and the self-test holds
the line with a word list.

The three deliberately echo the book's own arc: it opens at a family's kitchen
table (ch00) and closes at the graduate's own (ch17, ch18). Nobody's field of
work appears, because any workplace would imply a major and the book's whole
argument is that the major is the open question.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from workers_ai import credentials, generate                 # noqa: E402
from build_book_opener import (                              # noqa: E402
    OPENER_STYLE, CAMERAS, CAM_DOOR, CAM_ACROSS, CAM_BEHIND, SIZE, STEPS)

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
MASTERS = ROOT / "brand"

PANEL = SIZE                      # the openers' 1792x896, so nothing is cropped
LABEL_H = 104
ROW_LABEL_H = 64
PAD_BOT = 32
MUTED = (110, 110, 110)
FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"
INK = (28, 28, 28)
GROUND = (250, 249, 247)

# THE SAME STUDENT IN ALL THREE PANELS, and this constant is the only thing
# holding her together. klein4b takes no character reference, so continuity is
# whatever the words carry and nothing else: there is no mechanism here that can
# make two generations share a face. What a frozen description CAN hold is the
# type, which at book scale with faces drawn in a few lines is enough to read as
# one person: hair colour, length and how it sits, build, brow, and a colour
# register. Expect a recognisable person, not an identical one, and do not
# promise a reader more than that.
#
# The clothing VARIES and the palette does not. Fifteen years in the same
# sweater reads as a costume rather than a person; muted olive and navy across
# all three reads as someone who dresses the same way.
# A panel must not read as a verdict on whether the degree paid.
# "graduation gown", "diploma" and "certificate" WERE on this list and were
# removed on 2026-09-11: the panel is LABELLED "After graduation", so drawing one
# asserts nothing the label does not already say. What stays is the real risk.
# Props that draw a chart by their nature. An oscilloscope shipped once and put
# waveform traces on two screens in a book whose whole claim is that its figures
# are real: the contract already said no charts and no screens showing content,
# and naming the instrument defeated both. Anything with a display goes here.
SCREEN_PROPS = ("oscilloscope", "monitor", "dashboard", "television", "screen "
                "showing", "graph", "chart", "waveform")

VERDICT_WORDS = (
    "money", "cash", "coins", "wealthy", "rich", "poor", "struggling",
    "success", "successful", "failure", "celebrating", "triumphant",
    "worried", "anxious", "distressed", "regret", "proud", "reward",
    "trophy", "award", "luxury", "mansion", "sports car",
)

# DESCRIBE THE PERSON, NEVER THE SERIES. An early version opened "The same
# young woman appears in all three panels", and klein4b drew exactly that: one
# image containing three side by side panels of her. A prompt is not a note to a
# collaborator, it is a description of one picture, and anything in it about the
# SET becomes part of the FRAME. Continuity is carried by repeating the same
# attributes, never by asserting that continuity exists.
#
# GENERATED PER FAMILY, COMPOSED PER MOMENT. Each household's three images are
# made from one frozen PERSON string, so that household holds together across
# its own timeline; the three households are then assembled into one file per
# moment, so a chapter still takes the panel its argument is about. That is what
# buys continuity and placeability at the same time, and it is why nine
# illustrations produce three files.
#
# Continuity is a TYPE, not an identity. klein4b takes no character reference,
# so hair, build, brow and a colour register are doing all of the work. At book
# scale with faces drawn in a few lines that reads as one person. Do not promise
# a reader more.
PEOPLE = {
 "reyes": ("The Reyes family",
   "A young woman of slight build, with straight dark shoulder length hair "
   "tucked behind one ear and level dark eyebrows, dressed in muted olive "
   "green and navy"),
 "hall": ("The Hall family",
   "A tall young man with short dark curly hair and a square jaw, dressed in "
   "a muted grey blue shirt over dark trousers"),
 "nakamura": ("The Nakamura family",
   "A young man of medium build with straight black hair cut short, a "
   "straight nose and round wire glasses, dressed in a muted sage green "
   "jacket over a white collar"),
}

# THE MAJOR IS SIGNALLED BY AN OBJECT, NEVER BY WRITING. No lettering may appear
# (see the openers) and academic hood colours are a convention klein4b will not
# render reliably, so an object in the hands carries the field. Show the thing,
# never label it.
#
# THEO NAKAMURA IS DRAWN ON THE BUSINESS MANAGEMENT PATH, by the author's
# decision on 2026-09-11, and this comment is the guard that replaced the code
# one. `families.md` has him UNDECIDED between Psychology, Business Management
# and Mechanical Engineering, and chapter 11 exists because the spread between
# those three is the finding: never breaks even, ~$20,400, ~$208,000. So the
# picture shows ONE OF HIS THREE PATHS AND NOT HIS OUTCOME.
#
# WHAT THIS COSTS, so nobody spends it by accident: a caption that reads this
# panel as what Theo became contradicts the chapter it illustrates. His
# graduation panel still holds an empty gown, which is the half of the
# ambiguity a picture can still carry.
#
# His scene names NO SCREEN, deliberately. A presentation implies a projected
# slide and a slide is a chart, which is the one thing an illustration facing
# real federal figures may never contain. The far wall is bare, the same fix
# ch11's lecture room needed.
#
# family -> moment -> (scene, subject clause, rest of the cast, light, camera, seed)
SCENES = {
 "reyes": {
  "applying": (
    "a family kitchen table with an opened envelope and a folder on it, a bowl "
    "of fruit, a kettle on the counter and a chair pushed back and empty",
    "at seventeen, sitting at the table",
    "her mother sitting beside her reading the letter",
    "Evening, a single pendant lamp low over the table", CAM_ACROSS, 20261741),
  "graduation": (
    "the entrance of a university building close up, with wide stone steps, a "
    "pair of tall panelled doors standing open and an iron handrail",
    "at twenty two, large and close to us, wearing an open academic gown over "
    "her clothes and a flat mortarboard cap, a short stack of children's "
    "picture books under one arm, her whole body inside the frame",
    "nobody else in the picture",
    "Overcast morning, flat soft light", CAM_ACROSS, 20261742),
  "ten-years": (
    "a kindergarten classroom with low tables, small chairs, a soft rug in one "
    "corner, a low shelf of picture books and tall windows",
    "in her early thirties, her hair a little shorter, kneeling beside the rug "
    "and turned toward the room",
    "four small children sitting on the rug in front of her",
    "Morning light through the tall windows", CAM_ACROSS, 20261743),
 },
 "hall": {
  "applying": (
    "a family dining table with a folder open on it, a pen resting on the "
    "papers, a lamp at the end of the table and a bookcase behind",
    "at nineteen, sitting at the table",
    "his father sitting beside him leaning over the papers",
    "Night, one lamp and the room dark beyond it", CAM_ACROSS, 20261744),
  "graduation": (
    "the entrance of a university building close up, with wide stone steps, a "
    "pair of tall panelled doors standing open and an iron handrail",
    "at twenty two, large and close to us, wearing an open academic gown over "
    "his clothes and a flat mortarboard cap, a small circuit board and a coil "
    "of cable in one hand, his whole body inside the frame",
    "nobody else in the picture",
    "Overcast morning, flat soft light", CAM_ACROSS, 20261745),
  "ten-years": (
    "a hardware laboratory with long benches, a soldering iron in a stand, "
    "a bench power supply with plain round dials and no display, trays of "
    "small components and a window onto other buildings",
    "in his early thirties, standing at the bench with a circuit board in one "
    "hand",
    "a colleague working further along the bench",
    "Overcast daylight through the window", CAM_ACROSS, 20261746),
 },
 "nakamura": {
  "applying": (
    "an Ohio family kitchen table with three separate folders laid side by "
    "side on it, a kettle, two mugs and a window onto a yard",
    "at sixteen, standing at the table looking down at the three folders",
    "his two parents standing on the far side of the table",
    "Morning light from the window", CAM_ACROSS, 20261747),
  "graduation": (
    "the entrance of a university building close up, with wide stone steps, a "
    "pair of tall panelled doors standing open and an iron handrail",
    "at twenty two, large and close to us, wearing an open academic gown over "
    "his clothes and a flat mortarboard cap, both hands empty, his whole body "
    "inside the frame",
    "nobody else in the picture",
    "Overcast morning, flat soft light", CAM_ACROSS, 20261748),
  "ten-years": (
    "a conference room with a long table, task chairs around it, a water jug "
    "and glasses on the table, a plain bare wall at the far end of the room "
    "and a window along one side",
    "in his early thirties, standing at the far end of the table mid gesture, "
    "presenting to the room",
    "five colleagues seated around the table facing him",
    "Late afternoon light through the side window", CAM_ACROSS, 20261751),
 },
}

MOMENTS = {"applying": "Applying",
           "graduation": "After graduation",
           "ten-years": "Ten years after graduation"}



def build_prompt(family, moment):
    scene, subject, rest, light, camera, _seed = SCENES[family][moment]
    if camera not in CAMERAS:
        raise SystemExit(f"  {family}/{moment}: camera not one of three: {camera!r}")
    person = PEOPLE[family][1]
    rest = rest[0].upper() + rest[1:]
    return (f"Flat vector illustration of {scene}, {camera}. {person} {subject}. "
            f"{rest}. {light}. {OPENER_STYLE}")


def compose(moment, images):
    """The three households at one moment, stacked, each named in real type."""
    d0 = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    f_title = ImageFont.truetype(FONT_PATH, 56, index=1)
    f_row = ImageFont.truetype(FONT_PATH, 38, index=1)
    rows = list(PEOPLE)
    h = LABEL_H + len(rows) * (ROW_LABEL_H + PANEL[1]) + PAD_BOT
    page = Image.new("RGB", (PANEL[0], h), GROUND)
    d = ImageDraw.Draw(page)
    d.text((0, 24), MOMENTS[moment], font=f_title, fill=INK)
    y = LABEL_H
    for key in rows:
        d.text((0, y + 10), PEOPLE[key][0], font=f_row, fill=MUTED)
        y += ROW_LABEL_H
        page.paste(images[key], (0, y))
        y += PANEL[1]
    return page


def build(moment, dry_run=False, only=None):
    """Generate a moment. `only` reshoots one household and reuses the rest.

    The masters exist for exactly this. Without it, fixing one panel meant three
    calls and two good pictures thrown away, and klein4b is not deterministic so
    those two would not have come back the same.
    """
    print(f"\n{MOMENTS[moment]}\n")
    prompts = {k: (build_prompt(k, moment), SCENES[k][moment][5]) for k in PEOPLE}
    for k, (pr, seed) in prompts.items():
        print(f"  [{PEOPLE[k][0]}]  seed {seed}  ({len(pr)} chars)\n  {pr}\n")
    if dry_run:
        return
    acct, token = credentials()
    images = {}
    for k, (pr, seed) in prompts.items():
        master = MASTERS / f"book-timeline-{k}-{moment}.png"
        if only and k != only:
            if not master.exists():
                raise SystemExit(f"  no master for {k}/{moment}; build the whole moment once")
            images[k] = Image.open(master)
            print(f"  reusing {master.name}")
            continue
        im = generate(pr, seed, STEPS, acct, token, model="klein4b", size=PANEL)
        im.save(master)
        images[k] = im
    page = compose(moment, images)
    out = STATIC / f"book-timeline-{moment}.jpg"
    page.save(out, "JPEG", quality=88, progressive=True, optimize=True)
    print(f"  {out.relative_to(ROOT)}  ({page.width}x{page.height}, "
          f"{out.stat().st_size // 1024} KB)")


def _check():
    """Every assertion, callable on its own so a control can exercise it.

    The negative controls used to call self_test(), which called them, which
    called self_test(): a thousand frames deep and a RecursionError instead of a
    check. A control must drive the CHECKER, never the suite that owns it.
    """
    assert list(MOMENTS) == ["applying", "graduation", "ten-years"]
    assert set(SCENES) == set(PEOPLE), "a household has people or scenes, not both"
    seeds = [v[5] for fam in SCENES.values() for v in fam.values()]
    assert len(seeds) == 9 and len(set(seeds)) == 9, "a seed is reused"

    for fam in SCENES:
        assert set(SCENES[fam]) == set(MOMENTS), fam
        person = PEOPLE[fam][1]
        assert person.strip(), f"{fam}: no person described"
        for moment in MOMENTS:
            pr = build_prompt(fam, moment)
            assert OPENER_STYLE in pr, f"{fam}/{moment}: lost the style contract"
            assert person in pr, f"{fam}/{moment}: lost the person"
            assert len(pr) <= 1500, f"{fam}/{moment}: {len(pr)} chars"
            scene, subject, rest, *_ = SCENES[fam][moment]
            blob = (scene + " " + subject + " " + rest).lower()
            bad = [w for w in VERDICT_WORDS if w in blob]
            assert not bad, f"{fam}/{moment} asserts an outcome: {bad}"
            screens = [w for w in SCREEN_PROPS if w in blob]
            assert not screens, f"{fam}/{moment} names a prop that draws: {screens}"

    # Theo's GRADUATION panel still carries no field object: the gown is empty
    # and the ambiguity chapter 11 turns on survives there. His ten year panel
    # is on the Business Management path by decision. See the comment above.
    grad = " ".join(SCENES["nakamura"]["graduation"][:3]).lower()
    for w in ("circuit", "picture book", "wrench", "drafting", "stethoscope"):
        assert w not in grad, f"nakamura/graduation names a field: {w}"


def _control(label, mutate, restore, expect):
    """Plant a defect, require _check to name it, put it back."""
    mutate()
    try:
        _check()
    except (AssertionError, SystemExit) as e:
        assert expect in str(e), f"{label}: caught the wrong thing: {e}"
    else:
        raise AssertionError(f"{label}: the defect was accepted")
    finally:
        restore()


def self_test():
    """Contract, continuity, no verdict, and four controls. No network."""
    _check()

    saved = SCENES["reyes"]["applying"]
    _control("fourth camera",
             lambda: SCENES["reyes"].__setitem__("applying", saved[:4] + ("from a low angle", saved[5])),
             lambda: SCENES["reyes"].__setitem__("applying", saved),
             "camera not one of three")
    _control("a verdict panel",
             lambda: SCENES["reyes"].__setitem__("applying", (saved[0] + " and a pile of money",) + saved[1:]),
             lambda: SCENES["reyes"].__setitem__("applying", saved),
             "asserts an outcome")

    saved_hall = PEOPLE["hall"]
    _control("a household with nobody in it",
             lambda: PEOPLE.__setitem__("hall", (saved_hall[0], "")),
             lambda: PEOPLE.__setitem__("hall", saved_hall),
             "no person described")

    saved_hall = SCENES["hall"]["ten-years"]
    _control("a prop that draws a chart",
             lambda: SCENES["hall"].__setitem__("ten-years", (saved_hall[0] + " and an oscilloscope",) + saved_hall[1:]),
             lambda: SCENES["hall"].__setitem__("ten-years", saved_hall),
             "names a prop that draws")

    saved_theo = SCENES["nakamura"]["graduation"]
    _control("a field put into Theo's empty gown",
             lambda: SCENES["nakamura"].__setitem__("graduation", (saved_theo[0] + " and a drafting board",) + saved_theo[1:]),
             lambda: SCENES["nakamura"].__setitem__("graduation", saved_theo),
             "names a field")

    longest = max(len(build_prompt(f, m)) for f in SCENES for m in MOMENTS)
    print(f"  self-test OK: 3 households x 3 moments, 5 controls, "
          f"longest prompt {longest} chars")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--moment", choices=list(MOMENTS))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--family", choices=list(PEOPLE),
                    help="reshoot one household, reuse the other masters")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.list or not a.moment:
        for m, label in MOMENTS.items():
            print(f"{m:12s} {label}")
            for k in PEOPLE:
                print(f"             {PEOPLE[k][0]:22s} seed {SCENES[k][m][5]}")
        return
    build(a.moment, a.dry_run, only=a.family)


if __name__ == "__main__":
    main()

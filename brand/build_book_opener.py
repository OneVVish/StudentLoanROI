#!/usr/bin/env python3
"""Chapter openers for the book, generated with Cloudflare Workers AI (klein4b).

    python3 brand/build_book_opener.py --list
    python3 brand/build_book_opener.py --chapter ch09 --dry-run   # no call
    python3 brand/build_book_opener.py --chapter ch09             # the anchor
    python3 brand/build_book_opener.py --self-test                # no network

WHY NOT FIREFLY, WHICH WAS THE PLAN. Firefly Services exposes a style-reference
image, which would have pinned openers 00 to 18 to opener 09, and that mechanism
was the whole reason to take on a second vendor. It is unreachable: a Firefly or
Creative Cloud subscription buys the Firefly APP, while Firefly SERVICES is a
separately sold entitlement and its API never appears in the Adobe Developer
Console without one. Confirmed 2026-09-10 by going to add it and finding nothing
to add.

SO THE STYLE CONTRACT IS ALL THERE IS. klein4b takes a prompt, a size and a seed
and nothing else: nineteen images agree only insofar as `OPENER_STYLE`, the three
cameras and nineteen scene lines agree. That makes the ch06/ch09/ch15 checkpoint
the load-bearing step rather than a formality, because it is the ONLY place the
style can be judged before sixteen more images are generated against it.

WHY klein4b AND NOT klein9b. Black Forest Labs released FLUX.2 [klein] 4B under
Apache-2.0 and 9B under the FLUX Non-Commercial License. The book is sold, so 9B
is unusable here whatever it costs and however well it draws; `build_ai_hero.py`
records the same refusal and rejects the name outright. Picking by parameter
count gets the licence question exactly backwards.

SIZE IS 1792x896 AND IS ASKED FOR DIRECTLY. klein4b takes width and height, so
nothing is cropped: the frame the prompt describes is the frame the book gets.
Both are multiples of 16, which the FLUX.2 family requires. At a 4.5in text block
on a 6x9 trim that is about 400 dpi, against the charts' 360.

THIS FILE DOES NOT IMPORT THE GUIDES, and that separation is deliberate. The
Workers AI plumbing lives in `workers_ai.py` and both generators import it; the
site's hero generator and this one share no constants and cannot break each
other. In particular nothing here goes near `crop_to_band` or `compose_hero`,
which are in `build_ai_hero.py` and are built for a 3.5:1 web banner: the first
would crop this to 1600x464 and the second would squash it there with
`imshow(aspect="auto")` and add the site's curved bottom edge. A printed opener
is not a site header.

WHAT THIS MUST NEVER GENERATE. An opener faces a page of real federal figures.
`build_ai_hero.py` states the rule for the site and it is STRICTER here: no
charts, no grids of numbers, no dashboards, nothing resembling a figure, however
illegible.

NO FACES, AND NOT ONLY FOR CRAFT REASONS. Every figure is turned away. A botched
face is the most visible failure on a printed page and `figures.md` already
commits the book's figures to none, but the reason that outranks both is that a
face attached to a money figure is a claim about who is in this situation.

REPRODUCIBILITY. Every scene pins a seed, and here that is the whole
reproducibility story rather than half of it: same prompt, same seed, same
picture. A REJECT GETS A NEW SEED, never an edited prompt at the old seed, which
is the house rule `build_ai_hero.py` records and its duplicate-key bug already
broke once.

THE STYLE CONTRACT IS ONE FROZEN CONSTANT, applied by `build_prompt` and nowhere
else, so it is structurally impossible for opener 14 to lose a clause. Editing it
invalidates all nineteen and means all nineteen get new seeds. Freeze it after
the checkpoint.

COST. About a tenth of a cent an image, so the whole set with rejects is a few
cents.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from workers_ai import (                                     # noqa: E402
    credentials, generate)

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
MASTERS = ROOT / "brand"

SIZE = (1792, 896)          # 2:1, both multiples of 16, ~400 dpi at 4.5in
STEPS = 4

# ---------------------------------------------------------------------------
# The style contract. FROZEN after the checkpoint: see the docstring.
#
# The recurring phrases are lifted from `build_ai_hero.py`'s
# `college-money-calendar` prompt so the openers read as the same illustrator
# who drew that dorm room. Three clauses are added that the anchor lacks: flat
# color with no gradients (flatness is implied by "flat vector" and not always
# honored), everything well inside the picture (learned in the
# `refinancing-federal-student-loans` prompt), and light-to-dark separation,
# which is the insurance against the still-open question of whether the book's
# interior prints in grayscale.
#
# The negatives are IN the prompt rather than beside it because this endpoint
# has no negative-prompt field. That is a real difference from the Firefly draft
# of this file and is why the constant is one string rather than two.
#
# REWRITTEN ONCE, AT THE CHECKPOINT, 2026-09-10, and this is what it fixed.
# The colour clause used to read "soft green and navy accents over warm neutral
# WOOD TONES". Seventeen of the nineteen scenes are interiors full of wood and
# held the register effortlessly; the two exteriors have no wood for the muting
# to attach to, and both ch15 seeds came back with saturated grass, orange brick
# and cyan sky. The second one also lost the outlines entirely, which looks like
# a downstream symptom rather than a separate fault: a picture already off
# register on colour is the one that stops looking drawn. So the palette is now
# stated in its OWN right rather than by reference to a material that may not be
# in the scene, and the outline is a leading sentence rather than a trailing
# clause in a list. Freeze it here.
#
# ONE CLAUSE RESTORED, 2026-09-10, and it is a restoration rather than a second
# rewrite. "No logos" was in the Firefly draft's separate negative string and was
# dropped when the negatives were folded back into this one constant. klein4b put
# an unmistakable Apple mark on a laptop lid in ch06 the first time the gap was
# reachable. A brand mark on a page of a book that is sold is a different problem
# from a squiggle, and nothing else in the contract excluded it.
OPENER_STYLE = (
    "Every edge drawn with a clean bold outline of even weight. Flat color "
    "with no gradients. Muted desaturated palette throughout, no bright or "
    "saturated color anywhere, soft green and navy accents over warm neutral "
    "tones. Subtle shadows, generous margins. Strong light to dark separation "
    "so the scene reads without color. Faces drawn simply in the same flat "
    "style as the room, a few clean lines, calm and unexaggerated, no "
    "caricature. Every figure and object complete and well inside the "
    "picture, nothing touching or running off the edges of the frame. No text, no numbers, no letters, no labels, no signage, no "
    "posters with words, no charts, no screens showing content, no logos and "
    "no brand marks."
)


# Exactly three cameras, and that restriction is what stops nineteen images
# reading as nineteen illustrators. Worded to work outdoors as well as in.
CAM_DOOR = "seen from the doorway"
CAM_ACROSS = "seen from across the space at eye height"
CAM_BEHIND = "seen over the shoulder of the nearest figure"
CAMERAS = (CAM_DOOR, CAM_ACROSS, CAM_BEHIND)

# chapter -> (scene and props, cast, light, camera, seed)
#
# NO SCENE NAMES A WRITING SURFACE, and that rule was learned rather than
# designed. "A blank X with nothing written on it" is the construction that
# fails: it names the surface, and a negative clause elsewhere in a
# thousand-character prompt does not outweigh what the model thinks the object
# is. ch06's laptop kept its Apple mark through a reseed made WITH "no logos"
# already in the contract, and ch09's wall calendar produced a month header on
# its third seed. Four props were swapped out on 2026-09-10 for ones that carry
# no marks: the laptop, the calendar, a lecture room's whiteboard and clock (a
# clock face is digits), and a coffee counter's menu board. Two of the four had
# not been generated yet, so they cost nothing; the other two cost four rejects.
#
# TWO MORE ON 2026-09-11, both props nobody had filed as writing surfaces. A
# "tour group" carries a banner the way a laptop carries a logo: two of three
# ch15 seeds produced one, so the scene names the people rather than the
# institution. And a cardboard box carries a shipping label, so ch09's move-in
# is suitcases, a duffel and a laundry basket. The rule generalises past
# surfaces: ANY prop whose real-world version usually has writing on it will
# arrive with writing on it, however the prompt describes it.
#
# Cast tracks the parts: family, then classmates, then friends, then the
# graduate. Light is the part signature. No two neighbors share a room, with one
# deliberate exception: 17 and 18 are the same kitchen back to back, closing the
# book where ch00 opened it, at a table with people at it.
SCENES = {
    "ch00": ("a family kitchen table with a stack of unopened mail, a bowl of " "fruit, a kettle on the counter and a third chair pushed back and " "empty",
             "a parent and a teenager sitting side by side at the table, the "
             "parent looking at the mail and the teenager looking at the "
             "parent",
             "Evening, a single pendant lamp low over the table",
             CAM_BEHIND, 20261404),
    "ch01": ("a high school counselor's office with a corner of a desk, a " "swivel chair, a backpack on the floor, a filing cabinet and a " "potted plant by the window",
             "a student and a parent seated at the corner of the desk, both "
             "listening",
             "Late afternoon light across the desk", CAM_DOOR, 20261405),
    "ch02": ("a campus path running between lawns toward a brick gate and " "brick buildings, with railings, a bicycle rack and bare young " "trees",
             "a parent and a student walking away along the path, the student "
             "glancing back over one shoulder",
             "Overcast morning, flat soft light", CAM_BEHIND, 20261301),
    "ch03": ("a family kitchen table with a shoebox of folders open on it, " "loose papers, a coffee pot and an empty chair at the near end",
             "two parents leaning over the open folders together",
             "Morning light from a window to the left", CAM_BEHIND, 20261406),
    "ch04": ("a family living room with a low table, one opened envelope, a " "sofa with cushions, a rug and a lamp in the corner",
             "two adults on the sofa with the opened letter between them and "
             "a younger sibling sitting on the rug",
             "Evening, warm lamplight from one corner", CAM_ACROSS, 20261407),
    "ch05": ("a community college cafeteria with long tables, trays, a window " "wall onto a parking lot, a napkin dispenser and stacked chairs",
             "two students at a table with their trays and a third setting "
             "hers down to join them",
             "Midday, bright even light through the window wall",
             CAM_ACROSS, 20261408),
    "ch06": ("a small college dorm room with a single bed and a plain blanket, " "a wooden desk with a mug and a small desk lamp, a shelf of books " "and a backpack on the floor",
             "one student working at the desk and a roommate sitting on the "
             "bed",
             "Late afternoon light through the window", CAM_DOOR, 20261401),
    "ch07": ("a family kitchen table with a pen resting on it, a folded " "newspaper, a mug and the student's chair empty at the far end",
             "two parents alone at the table, one of them holding the pen",
             "Night, one light above the table and the room beyond it dim but "
             "still legible rather than black",
             CAM_BEHIND, 20261802),
    "ch08": ("a living room with a sofa, a side table, a single lamp, a folded " "blanket and a bookcase against the far wall",
             "a parent and an adult child on the sofa and a third adult "
             "standing behind it",
             "Night, one lamp and the corners in soft shadow rather than "
             "blackness",
             CAM_ACROSS, 20261803),
    "ch09": ("a small college dorm room on move-in day, half unpacked, with two "
             "open suitcases, a duffel bag, a laundry basket, a folded "
             "duvet on the floor, a bare mattress and an empty desk",
             "a parent in the open doorway and a student kneeling beside one "
             "of the suitcases",
             "Morning light through the window", CAM_ACROSS, 20261402),
    "ch10": ("a university library with a long table under low hanging lamps, " "stacked books, a water bottle and tall shelves behind",
             "two students working together at the long table and a third "
             "asleep further down with their head on their arms",
             "Evening, pooled light from the hanging lamps", CAM_ACROSS, 20261411),
    "ch11": ("a university lecture classroom seen from the back rows, with " "tiered desks, a bare front wall and tall windows along one " "side",
             "about a dozen students at the tiered desks, one of them leaning "
             "over to say something to a friend",
             "Daylight through the side windows", CAM_BEHIND, 20261412),
    "ch12": ("a campus coffee counter with an espresso machine, a stack of " "cups, a tip jar and a shelf of syrup bottles",
             "a student in an apron behind the counter and two friends "
             "waiting in front of it",
             "Early evening, warm light over the counter", CAM_ACROSS, 20261413),
    # THREE NIGHT SCENES ASKED FOR DARKNESS AND GOT IT. Measured 2026-09-11
    # across the nineteen: the set's median mean brightness is 132 of 255, and
    # ch13 came back at 27.7, ch07 at 57.6 and ch08 at 60.2. On a printed page
    # that is ink rather than atmosphere. All three said some version of "the
    # rest of the room dark", "dark beyond it", "deep shadow", and the model is
    # right to draw what it is told. The night stays in all three, because in
    # ch07 and ch08 it is doing real work; the word that had to go is the one
    # asking for an absence of light rather than a low level of it.
    #
    # ch13 SAID "the rest of the room dark" AND GOT IT: seed 20261414 came back
    # near black, the only opener in the set that could not sit on a printed page
    # without soaking it. A chapter about long roads wants late and quiet, not
    # unlit. The light line asks for low light rather than darkness now, and the
    # cast says one student and says they are large in the frame, because the
    # dark version drew two small ones.
    "ch13": ("a university library nearly empty at night, one lit carrel among " "many dark ones, a stack of books, a chair pushed out and long " "shelves receding",
             "one student alone at the lit carrel, large in the frame",
             "Late evening, that carrel's lamp lit and the room beyond it in "
             "soft low light rather than darkness",
             CAM_ACROSS, 20261801),
    "ch14": ("a workshop and lab classroom with workbenches, hand tools on a " "pegboard, a vise, safety goggles and a roll-up door at the back",
             "three students working at the benches and an instructor leaning "
             "in beside one of them",
             "Daylight through high windows", CAM_ACROSS, 20261415),
    "ch15": ("a university quad with mown grass, a paved crossing path, " "benches, bags dropped on the ground and brick buildings behind",
             "four students sitting together on the grass and three or four "
             "others walking along the path further back",
             "Afternoon, long soft shadows across the grass",
             CAM_ACROSS, 20261403),
    "ch16": ("a financial aid office with two chairs turned to face each " "other, a desk pushed to one side, a coat over a chair back and a " "window with blinds half open",
             "two adults seated facing each other across the turned chairs",
             "Late afternoon light through the blinds", CAM_DOOR, 20261601),
    "ch17": ("a small apartment kitchen with a two person table, a stack of " "mail, a single mug, a kettle and a roommate's coat hanging by " "the door",
             "one adult alone at the table",
             "Pale early morning light, the room still half dark",
             CAM_BEHIND, 20261501),
    "ch18": ("the same small apartment kitchen, the mail now a single closed " "folder on the two person table, a coin jar on the shelf, two " "mugs and the same kettle",
             "the same adult and a friend at the table together",
             "Evening, warm light over the table", CAM_BEHIND, 20261418),
}

# Long enough to dilute rather than a hard API limit: FLUX encodes the prompt
# with T5, which takes far more than this, but every clause past a point
# competes with the ones that matter. The cap is a drafting discipline.
#
# 1024 until the checkpoint rewrite, which put ch09 one character over at 1025.
# Raised rather than trimming a scene, because the constant grew for a reason
# and the alternative was editing a scene line to fit a budget, which is the
# tail wagging the dog. Anything that needs more than this is a scene carrying
# too many props.
PROMPT_LIMIT = 1100


def build_prompt(chapter: str) -> str:
    """One scene plus the frozen style contract, in that order.

    Concatenated here and nowhere else, which is what makes it impossible for
    an opener to ship without a clause.
    """
    scene, cast, light, camera, _seed = SCENES[chapter]
    if camera not in CAMERAS:
        raise SystemExit(f"  {chapter}: camera is not one of the three: {camera!r}")
    cast = cast[0].upper() + cast[1:]
    return (f"Flat vector illustration of {scene}, {camera}. {cast}. "
            f"{light}. {OPENER_STYLE}")


def write(im, chapter: str):
    """The master PNG and the committed JPEG. No crop: the size was requested.

    The master is lossless and gitignored; the JPEG is what the book and the
    preview read, at the same quality as the twenty-two `book-chart-*.jpg`
    already committed beside it.
    """
    if im.size != SIZE:
        raise SystemExit(f"  got {im.size}, asked for {SIZE}")
    master = MASTERS / f"book-opener-{chapter}.png"
    im.save(master)
    jpg = STATIC / f"book-opener-{chapter}.jpg"
    im.save(jpg, "JPEG", quality=88, progressive=True, optimize=True)
    print(f"  {master.relative_to(ROOT)}  (master, {im.size[0]}x{im.size[1]})")
    print(f"  {jpg.relative_to(ROOT)}  ({jpg.stat().st_size // 1024} KB)")


def self_test():
    """Every prompt builds, fits the cap, and keeps the contract.

    No network and no credentials. The negative controls are the last three:
    each plants the failure this file exists to prevent and must be caught.
    """
    assert len(SCENES) == 19, len(SCENES)
    assert sorted(SCENES) == [f"ch{i:02d}" for i in range(19)]

    seeds = [v[4] for v in SCENES.values()]
    assert len(set(seeds)) == 19, "a seed is reused"

    assert not any(n % 16 for n in SIZE), f"{SIZE} is not a multiple of 16"
    assert SIZE[0] == 2 * SIZE[1], f"{SIZE} is not 2:1"

    for ch in SCENES:
        p = build_prompt(ch)
        assert len(p) <= PROMPT_LIMIT, f"{ch}: prompt is {len(p)} chars"
        assert OPENER_STYLE in p, f"{ch}: lost the style contract"
        assert SCENES[ch][1].strip(), f"{ch}: nobody is in the room"
        assert "" in p, f"{ch}: cast may show a face"
        if SCENES[ch][3] == CAM_DOOR:
            assert "doorway" not in SCENES[ch][1], \
                f"{ch}: the camera stands where the cast stands"

    # No two NEIGHBORING chapters share a room, except 17 and 18 by design.
    chs = [f"ch{i:02d}" for i in range(19)]
    for a, b in zip(chs, chs[1:]):
        if (a, b) == ("ch17", "ch18"):
            continue
        wa = set(SCENES[a][0].split()[:6])
        assert wa != set(SCENES[b][0].split()[:6]), f"{a} and {b} share a room"

    # Negative controls.
    saved = SCENES["ch00"]
    try:
        SCENES["ch00"] = (saved[0], saved[1], saved[2], "from a low angle", saved[4])
        try:
            build_prompt("ch00")
        except SystemExit:
            pass
        else:
            raise AssertionError("a fourth camera was accepted")

        SCENES["ch00"] = (saved[0] + " x" * 600, saved[1], saved[2], saved[3], saved[4])
        assert len(build_prompt("ch00")) > PROMPT_LIMIT, "overlong prompt not caught"

        SCENES["ch00"] = (saved[0], "", saved[2], saved[3], saved[4])
        try:
            build_prompt("ch00")
        except (SystemExit, IndexError):
            pass
        else:
            raise AssertionError("an empty cast was accepted")
    finally:
        SCENES["ch00"] = saved

    print(f"  self-test OK: 19 scenes at {SIZE[0]}x{SIZE[1]}, longest prompt "
          f"{max(len(build_prompt(c)) for c in SCENES)}/{PROMPT_LIMIT} chars")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chapter", help="ch00 .. ch18")
    ap.add_argument("--list", action="store_true", help="show every scene")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt and make no call")
    ap.add_argument("--self-test", action="store_true",
                    help="check the contract, no network")
    ap.add_argument("--seed", type=int,
                    help="override the pinned seed: a REJECT GETS A NEW SEED, " "never an edited prompt at the old one")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.list or not args.chapter:
        for ch in sorted(SCENES):
            print(f"{ch}  seed {SCENES[ch][4]}  {SCENES[ch][3]}")
            print(f"      {SCENES[ch][2]}")
        return

    if args.chapter not in SCENES:
        raise SystemExit(f"  unknown chapter {args.chapter!r}")

    seed = args.seed or SCENES[args.chapter][4]
    prompt = build_prompt(args.chapter)
    print(f"\n{args.chapter}  seed {seed}  ({len(prompt)}/{PROMPT_LIMIT} chars)\n")
    print(f"  {prompt}\n")
    if args.dry_run:
        return

    acct, token = credentials()
    im = generate(prompt, seed, STEPS, acct, token, model="klein4b", size=SIZE)
    write(im, args.chapter)


if __name__ == "__main__":
    main()

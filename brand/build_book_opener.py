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

FACES ARE DRAWN, since the author reversed that on 2026-09-11. This paragraph
used to read "NO FACES, AND NOT ONLY FOR CRAFT REASONS. Every figure is turned
away", and it was still saying so months after the constant stopped agreeing.
The prohibition was REPLACED rather than deleted, because bare permission leaves
the model free to render at any level of detail and that is where the botched
face lives: the contract names the treatment instead, a few clean lines, calm
and unexaggerated. One reason for the original rule survives in `figures.md`
and is not a craft one, that a face attached to a money figure is a claim about
who is in this situation.

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
MASTERS = ROOT / "brand"

# WHERE THE BOOK ACTUALLY READS ITS FIGURES, which stopped being static/ on
# 2026-09-13 when the manuscript's figures moved to the private repo. This
# generator was not moved with them, so for three days every run SUCCEEDED and
# wrote its JPEG to a directory the book does not open: static/ holds no
# book-opener-*.jpg at all. The failure is silent in the worst way, because the
# script prints a path and a file size and both are true.
#
# Resolved the way build_book.py, build_latex.py and print_pages.py already
# resolve it: the book's own directory when it exists, the public one when it
# does not, so a clone without marketing/ still writes somewhere sensible.
BOOK_FIGURES = ROOT / "marketing" / "book" / "figures"
STATIC = BOOK_FIGURES if BOOK_FIGURES.is_dir() else ROOT / "static"

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


# A SECOND CONTRACT, ON TRIAL. Chapter 13's opener had to be regraded on
# 2026-09-16 because a flat-shaded night scene measured a mean luminance of 57
# against a set averaging 130 and printed as a dark block. That regrade is a
# patch on a symptom: line art on white paper has almost no tone to lose, so
# the whole class of problem stops existing rather than being corrected image
# by image. Chapter 7 at 74 is the next candidate.
#
# IT IS A SEPARATE CONSTANT AND NOT AN EDIT, because build_timeline_panel.py
# imports OPENER_STYLE and asserts it survives into every prompt: changing it
# restyles nineteen openers and thirteen timeline masters, and by this file's
# own rule every one of them then needs a new seed. That is the commitment
# being tested, not the test. Promote this one and delete the flag if it lands.
#
# "NO CROSSHATCHING, NO STIPPLE, NO FINE TEXTURE" IS LOAD-BEARING, not taste.
# build_book.py packs illustrations at 1000px and quality 72 on the stated
# reasoning that they are flat line work with no texture, which is what JPEG
# compresses best and what downscaling barely touches. Crosshatch is the exact
# opposite. Keeping the washes flat is what keeps that packing valid.
#
# NAMING THE SURFACE IS WHAT DREW THE SURFACE. The first two renders came back
# with a torn deckled edge and paper grain, through a clause that said "no
# paper grain, no torn or deckled edge, no border and no visible paper sheet".
# That is the failing construction this file already records for props that
# carry writing: "a blank X with nothing written on it" NAMES X and gets X. The
# word paper appeared three times in a contract asking for no paper, and the
# lead said "Pen and ink drawing of", which is a drawing on a sheet.
#
# So the medium is now named by its marks rather than its support, "Ink and
# grey wash illustration", the word paper appears nowhere, and the frame is
# asserted POSITIVELY: the image fills the whole rectangular frame edge to
# edge. A positive instruction about the frame beats a negative one about the
# sheet, which is the same move as swapping a cardboard box for a suitcase.
#
# The negatives are copied VERBATIM from OPENER_STYLE. Every clause in them
# exists because a prop carried writing, and a style trial is no reason to
# re-earn any of it.
OPENER_STYLE_WASH = (
    "Every edge drawn with a confident black ink outline, "
    "the line varying in weight the way a nib does. No color: the only tones "
    "are two or three flat washes of warm grey, laid in large simple areas, "
    "with clean white doing most of the work. No gradients, no "
    "crosshatching, no stipple, no fine texture. The image fills the whole "
    "rectangular frame edge to edge. Subtle shadows, generous "
    "margins. Strong light to dark separation so the scene reads without "
    "color. Faces drawn simply in the same few clean lines as the room, calm "
    "and unexaggerated, no caricature. Every figure and object complete and "
    "well inside the picture, nothing touching or running off the edges of "
    "the frame. No text, no numbers, no letters, no labels, no signage, no "
    "posters with words, no charts, no screens showing content, no logos and "
    "no brand marks."
)

# The same drawing with the house accents kept, because the EPUB is in colour
# even where the interior is not. Shot beside the grey one rather than argued
# about: at a tenth of a cent an image the comparison is cheaper than the
# discussion.
OPENER_STYLE_WASH_ACCENT = OPENER_STYLE_WASH.replace(
    "No color: the only tones are two or three flat washes of warm grey",
    "Almost no color: soft green and navy as the only hues, over two or three "
    "flat washes of warm grey")

STYLES = {"flat": OPENER_STYLE, "wash": OPENER_STYLE_WASH,
          "wash-accent": OPENER_STYLE_WASH_ACCENT}


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
    # A LIVING ROOM WITH AN OPENED ENVELOPE WAS HERE, and it was the weakest
    # opener in the book for three reasons at once. It repeated ch08's room, it
    # told ch00's and ch03's story a third time (a family at home with
    # paperwork, three of the first five openers), and it showed the letter
    # ARRIVING, which is chapter 3's moment and not this chapter's. Chapter 4 is
    # the price you would actually pay: in state against out of state, the same
    # seat at two prices, a family shopping across a state line. That wants a
    # PLACE and a distance, not another table. Reshot 2026-09-12.
    #
    # THE CAR IS SIDE-ON ON PURPOSE. A number plate is writing, and this file's
    # own rule is that a prop carrying writing carries writing however firmly
    # the contract says otherwise. A car seen from the side has no plate in
    # frame, which is the scene-level fix rather than a negative clause.
    "ch04": ("a campus visitor parking lot, one parked car seen side on, a "
             "painted bay line and university buildings in muted brown grey "
             "brick across the way",
             "exactly three people, two parents and their teenager, standing "
             "at full length beside the car with their backs to us and looking "
             "across at the buildings",
             "Late afternoon, low sun, the buildings warm on one side",
             CAM_BEHIND, 20261408),
    # SEED COLLISION, FOUND 2026-09-16, AND ONE HALF OF IT IS UNRECOVERABLE.
    # This entry and ch04 both read 20261408 and had done long enough for the
    # self-test to be red the whole time, which is the real finding: this
    # script's --self-test is not in CI, so a failing contract check sat here
    # unnoticed while nineteen images were generated against it.
    #
    # ch04 carries a "Reshot 2026-09-12" note, so 20261408 is most likely its
    # own and this one is the stale copy, but that cannot be established from
    # anything on disk. The seed is renumbered here so the check means
    # something again; the committed book-opener-ch05.jpg was made under a seed
    # this file can no longer name. That costs nothing reproducible, because
    # klein4b cannot reproduce a seed at all (verified 2026-09-11), and a seed
    # here documents which dice were thrown rather than reproducing the throw.
    "ch05": ("a community college cafeteria with long tables, trays, a window " "wall onto a parking lot, a napkin dispenser and stacked chairs",
             "two students at a table with their trays and a third setting "
             "hers down to join them",
             "Midday, bright even light through the window wall",
             CAM_ACROSS, 20261605),
    "ch06": ("a small college dorm room with a single bed and a plain blanket, " "a wooden desk with a mug and a small desk lamp, a shelf of books " "and a backpack on the floor",
             "one student working at the desk and a roommate sitting on the "
             "bed",
             "Late afternoon light through the window", CAM_DOOR, 20261401),
    # A FOLDED NEWSPAPER WAS HERE AND IT CAME BACK AS A NEWSPAPER, open, with
    # columns of simulated text and grey image blocks, in a book whose whole
    # claim is that its figures are real. Reshot 2026-09-12. It is the rule this
    # file already records: a negative clause does not beat a strong object
    # association, and a newspaper is the strongest of them, being nothing but
    # text. Reading glasses carry the same meaning, somebody has been reading
    # something hard, and their real-world version has nothing written on it.
    "ch07": ("a family kitchen table with a pen resting on it, a pair of "
             "reading glasses folded beside it, a mug and the student's "
             "chair empty at the far end",
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
#
# 1100 UNTIL 2026-09-16, AND IT HAD ALREADY BEEN BREACHED BY THE COMMITTED SET:
# ch04 measured 1125 on the FLAT contract, so this assertion was failing on the
# nineteen openers the book ships. It went unseen because --self-test is not in
# CI and the seed-collision assert above it fired first, so the run stopped
# before reaching this one. Two red checks, one script nobody ran.
#
# SO IT NOW MEASURES THE SCENE, WHICH IS WHAT IT ALWAYS MEANT TO POLICE. The
# cap exists so a scene does not accumulate props; it measured the whole
# prompt, so every rewrite of the style contract moved it, three times, never
# once for the reason the cap was written. Raising it a fourth time for the
# pen-and-ink trial would have been the tail wagging the dog twice over.
#
# The scene portion runs 290 to 429 across the nineteen, so 480 is real
# headroom and still refuses a scene carrying too many props. A contract now
# costs what it costs: flat is 696, the trial contracts 899 and 930, and none
# of them can push a scene over its own budget.
SCENE_LIMIT = 480


def build_prompt(chapter: str, style: str = "flat") -> str:
    """One scene plus the frozen style contract, in that order.

    Concatenated here and nowhere else, which is what makes it impossible for
    an opener to ship without a clause.
    """
    scene, cast, light, camera, _seed = SCENES[chapter]
    if camera not in CAMERAS:
        raise SystemExit(f"  {chapter}: camera is not one of the three: {camera!r}")
    if style not in STYLES:
        raise SystemExit(f"  no such style: {style!r}. One of {sorted(STYLES)}.")
    cast = cast[0].upper() + cast[1:]
    # The opening noun belongs to the style, not to the scene: "Flat vector
    # illustration of a library" and "Pen and ink drawing" in one prompt is two
    # illustrators in one sentence.
    lead = ("Flat vector illustration of" if style == "flat"
            else "Ink and grey wash illustration of")
    return (f"{lead} {scene}, {camera}. {cast}. "
            f"{light}. {STYLES[style]}")


def write(im, chapter: str, out=None):
    """The master PNG and the committed JPEG. No crop: the size was requested.

    The master is lossless and gitignored; the JPEG is what the book and the
    preview read, at the same quality as the twenty-two `book-chart-*.jpg`
    already committed beside it.
    """
    if im.size != SIZE:
        raise SystemExit(f"  got {im.size}, asked for {SIZE}")
    master = (out.with_suffix(".png") if out
              else MASTERS / f"book-opener-{chapter}.png")
    master.parent.mkdir(parents=True, exist_ok=True)
    im.save(master)
    jpg = out or STATIC / f"book-opener-{chapter}.jpg"
    jpg.parent.mkdir(parents=True, exist_ok=True)
    im.save(jpg, "JPEG", quality=88, progressive=True, optimize=True)
    mwhere = master.relative_to(ROOT) if ROOT in master.parents else master
    print(f"  {mwhere}  (master, {im.size[0]}x{im.size[1]})")
    where = jpg.relative_to(ROOT) if ROOT in jpg.parents else jpg
    print(f"  {where}  ({jpg.stat().st_size // 1024} KB)")


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
        assert len(p) - len(OPENER_STYLE) <= SCENE_LIMIT, \
            f"{ch}: scene is {len(p) - len(OPENER_STYLE)} chars"
        assert OPENER_STYLE in p, f"{ch}: lost the style contract"
        assert SCENES[ch][1].strip(), f"{ch}: nobody is in the room"
        # WAS `assert "" in p`, which is true of every string and tested
        # nothing. It is the vestigial faceless guard, left behind when
        # the author reversed that rule on 2026-09-11, and an assertion
        # that cannot fail reads as coverage while being none. What is
        # worth asserting now is that the contract still SAYS how a face
        # is drawn, since that clause is the whole botched-face defence.
        assert "no caricature" in p, f"{ch}: lost the face treatment"
        # EVERY CONTRACT, NOT ONLY THE DEFAULT. A second style that nothing
        # checks is a second style that silently loses a clause, which is the
        # exact failure one frozen constant was introduced to make impossible.
        for name in STYLES:
            q = build_prompt(ch, name)
            assert STYLES[name] in q, f"{ch}/{name}: lost the style contract"
            scene_len = len(q) - len(STYLES[name])
            assert scene_len <= SCENE_LIMIT, \
                f"{ch}/{name}: scene is {scene_len} chars, over {SCENE_LIMIT}"
            assert "no brand marks" in q, f"{ch}/{name}: lost the negatives"
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
        assert (len(build_prompt("ch00")) - len(OPENER_STYLE)) > SCENE_LIMIT, \
            "overlong scene not caught"

        SCENES["ch00"] = (saved[0], "", saved[2], saved[3], saved[4])
        try:
            build_prompt("ch00")
        except (SystemExit, IndexError):
            pass
        else:
            raise AssertionError("an empty cast was accepted")
    finally:
        SCENES["ch00"] = saved

    print(f"  self-test OK: 19 scenes at {SIZE[0]}x{SIZE[1]}, "
          f"longest scene "
          f"{max(len(build_prompt(c)) - len(OPENER_STYLE) for c in SCENES)}"
          f"/{SCENE_LIMIT} chars")


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
    ap.add_argument("--style", choices=sorted(STYLES), default="flat",
                    help="the style contract. flat is the nineteen committed "
                         "openers; wash and wash-accent are the 2026-09-16 "
                         "pen-and-ink trial and write beside them, never over "
                         "them, until one is promoted")
    ap.add_argument("-o", "--out",
                    help="write the JPEG here instead of the book's figure "
                         "directory, for a trial that must not overwrite a "
                         "committed opener")
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
    prompt = build_prompt(args.chapter, args.style)
    # A NON-DEFAULT STYLE AT THE PINNED SEED IS A MISTAKE, and a quiet one: the
    # pinned seed belongs to the picture the committed opener already is, so
    # reusing it for a different contract makes the two impossible to talk
    # about afterwards. Same rule as a reject, arrived at from the other side.
    if args.style != "flat" and not args.seed:
        raise SystemExit(f"  --style {args.style} needs its own --seed; "
                         f"{args.chapter}'s {seed} belongs to the flat one")
    scene_len = len(prompt) - len(STYLES[args.style])
    print(f"\n{args.chapter}  seed {seed}  style {args.style}  "
          f"(scene {scene_len}/{SCENE_LIMIT}, prompt {len(prompt)})\n")
    print(f"  {prompt}\n")
    if args.dry_run:
        return

    acct, token = credentials()
    im = generate(prompt, seed, STEPS, acct, token, model="klein4b", size=SIZE)
    write(im, args.chapter, Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()

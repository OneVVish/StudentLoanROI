"""Compose the Parent PLUS senior-year hero from two generated jars.

Three prompts for "four jars, the first three identical" all came back as a
decline: the model reads four jars as a sequence. So build_ai_hero.py draws
TWO jars (slug parent-plus-senior-year-parts) and this lays them out as
three copies of the full one and the empty one: $20,000 three times, then
$5,000. Crops are by pixel on the recorded seed; regenerate the parts and
these numbers move.
"""
from PIL import Image

SRC = "static/guide-hero-parent-plus-senior-year-parts-klein4b.png"
OUT = "static/guide-hero-parent-plus-senior-year-cliff-klein4b.png"
OG = "static/guide-og-parent-plus-senior-year.png"
FULL = (380, 0, 740, 448)     # the full jar, with its shadow; stops above the source's bottom rule
EMPTY = (860, 0, 1200, 448)   # the near-empty jar

src = Image.open(SRC).convert("RGB")
bg = src.getpixel((10, 10))
full, empty = src.crop(FULL), src.crop(EMPTY)
canvas = Image.new("RGB", (1600, 464), bg)
slots = [200, 600, 1000, 1400]           # slot centres, four across
for cx, jar in zip(slots, (full, full, full, empty)):
    canvas.paste(jar, (cx - jar.width // 2, 0))
canvas.save(OUT, optimize=True)
og = Image.new("RGB", (1200, 630), bg)
scaled = canvas.resize((1200, int(464 * 1200 / 1600)))
og.paste(scaled, (0, (630 - scaled.height) // 2))
og.save(OG, optimize=True)
print("wrote", OUT, "and", OG)

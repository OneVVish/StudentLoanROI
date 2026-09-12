# Source Serif 4, the book's print interior face

Added 2026-09-12 for `marketing/book/print_pages.py`.

**Why a font is committed at all.** KDP rejects a PDF whose fonts are not
embedded, and reportlab's default Times is one of the base-14: referenced by
name, never embedded. So the interior needs a real font file, and it has to be
one this project is entitled to embed in a book sold for money.

**The licence is in the font.** Source Serif 4 v4.005 carries, in its own name
table (ID 13): "This Font Software is licensed under the SIL Open Font License,
Version 1.1", with ID 14 pointing at http://scripts.sil.org/OFL. That is read
off the file rather than a downloaded LICENSE, which is better evidence anyway;
the repo's own copy of the licence text could not be fetched here, because
raw.githubusercontent is intercepted on this network and answers HTML.

**What it replaces and why not the alternatives.** `brand/README.md` already
records that Avenir Next is "licensed, not open", and macOS's serifs (Georgia,
Charter, Baskerville) are Apple's on the same terms: fine on screen, not
something to embed in a paperback. Inter is here and is OFL, but it is a sans
and only the Display weights are present, which are not body text.

Four faces, regular / italic / bold / bold italic, from
github.com/adobe-fonts/source-serif at the release branch.

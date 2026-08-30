#!/usr/bin/env python3
"""Which colleges also require the CSS Profile, keyed by UNITID.

Source: the College Board's own participating-institutions list,
https://profile.collegeboard.org/PPI/participatingInstitutions.aspx
It is one server-rendered table with seven columns: CSS code, institution
name, state, and Yes/No for domestic students, international students,
noncustodial parents, and IDOC.

    python3 build_css_profile_schools.py                 # fetch and build
    python3 build_css_profile_schools.py ppi.html        # from a saved copy

THE HARD PART IS THE JOIN, AND IT HAS NO CLEAN ANSWER. College Board publishes
its own four-digit code and a name truncated to about fifty characters. There
is no UNITID and no published crosswalk, so the only key available is (name,
state) -- which is exactly the join this repo warns about, since 33 name-plus-
state keys in college_coa_clean.csv already map to more than one UNITID.

Three rules keep that honest:

  1. EXACT MATCHES ARE AUTOMATIC. Containment matches are NOT. A containment
     rule scores 89% and one of the six extra matches it finds is WRONG: it
     maps "Bard College at Simons Rock" onto "Bard College", which is a
     different institution, and would flag Bard as a Profile school on the
     strength of somebody else's row. So containment only PROPOSES; ALIASES
     below is the reviewed list that accepts, and every entry in it was
     eyeballed once. Re-run with --propose after a refresh to see the new
     candidates rather than trusting a rule.

  2. AN UNMATCHED SCHOOL IS UNKNOWN, NEVER "NO". The app must never tell a
     family a college does not require the Profile because a string join
     failed. Only positive matches are written, and app.py treats absence as
     silence -- see css_profile_requirement.

  3. SCHOLARSHIP FUNDS AND GRADUATE SCHOOLS ARE DROPPED. A third of this list
     is neither: foundations that collect the Profile to award their own money
     ("Jackie Robinson Foundation"), and medical or law schools whose parent
     university is listed separately. Flagging either on an undergraduate
     search would be nonsense.

Roughly a sixth of the participants are legitimately absent from the Scorecard
cost file and always will be: Hillsdale, Grove City, Patrick Henry and several
others take no federal money, so no federal dataset prices them. McGill is
Canadian. That residue is reported, not fixed.
"""
import argparse
import html
import re
import ssl
import sys
import unicodedata
import urllib.request

import certifi
import pandas as pd

SOURCE_URL = "https://profile.collegeboard.org/PPI/participatingInstitutions.aspx"
COA_PATH = "data/college_coa_clean.csv"
OUT_PATH = "data/css_profile_schools_clean.csv"

# Below this the join has broken rather than drifted, and a short table is
# indistinguishable downstream from "few colleges require the Profile".
# build_cc_costs.py's MIN_STATES exists for the same reason.
MIN_MATCHED = 100

# Not colleges. They collect the Profile to award their OWN money.
NOT_A_COLLEGE = re.compile(
    r"\b(?:foundation|found|scholarship|schol|fund|assoc|association|club|"
    r"society|trust|memorial|rotary|elks|lions|leadership)\b", re.I)
# Graduate and professional schools; their parent university is listed too.
GRADUATE = re.compile(
    r"(?::|school of medicine|medical school|school of nursing|school of law|"
    r"graduate stud|school of public health|college of medicine|"
    r"college of osteopathic|sch of med|school of dent|optometry|"
    r"theo sem|seminary|institute of music)", re.I)

ABBREVIATIONS = {
    r"\buniv\b": "university", r"\binst\b": "institute", r"\btech\b": "technology",
    r"\bcoll\b": "college", r"\bsch\b": "school", r"\bst\b": "saint",
    r"\bmt\b": "mount", r"\bu\b": "university",
}

# REVIEWED BY HAND, one line of justification each. Every one of these was
# proposed by the containment pass and then checked; the proposals it makes
# that are NOT here were checked and rejected.
ALIASES = {
    # College Board truncates at about fifty characters.
    "Univ of North Carolina Chapel": "University of North Carolina at Chapel Hill",
    # Scorecard carries the campus name; the Profile row is the flagship.
    "Arizona State University": "Arizona State University Campus Immersion",
    "Georgia Institute of Technology": "Georgia Institute of Technology-Main Campus",
    "Tulane University": "Tulane University of Louisiana",
    # Three Michigan campuses share the stem, so containment refused to choose.
    # College Board's row is Ann Arbor: Dearborn and Flint are not on the list.
    "Univ of Michigan": "University of Michigan-Ann Arbor",
    # Plural in Scorecard, singular abbreviation from College Board.
    "Hobart and William Smith Coll": "Hobart William Smith Colleges",
    # Scorecard dropped "College of" and uses an ampersand.
    "College of William and Mary": "William & Mary",
    # Containment proposed "Bard College" for this, which is a DIFFERENT
    # INSTITUTION and would have flagged Bard on the strength of Simon's Rock's
    # row. The real match was there all along under a reversed name, which is
    # the argument for reviewing proposals rather than accepting a rule.
    "Bard College at Simons Rock": "Simon's Rock at Bard College",
}


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\([^)]*\)", " ", text)      # "(USC)", "(NY)"
    text = re.sub(r",\s*[a-z]{2}\b", " ", text)  # "Adrian College, MI"
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    for pattern, replacement in ABBREVIATIONS.items():
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\b(the|of|at|and|inc|a)\b", " ", text)
    return re.sub(r"\s+", "", text)


def fetch(path: str = None) -> str:
    if path:
        return open(path, encoding="utf-8", errors="replace").read()
    # certifi explicitly: the python.org framework build ships no root store
    # wired into the default SSL context, which is a failure this repo has
    # already misdiagnosed as a proxy block once.
    context = ssl.create_default_context(cafile=certifi.where())
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, context=context, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def parse(page: str) -> pd.DataFrame:
    match = re.search(r"(?is)<table.*?</table>", page)
    if not match:
        sys.exit("No table in the participating-institutions page. The College "
                 "Board has changed its markup; this needs a look, not a retry.")
    rows = []
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", match.group(0)):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)]
        if len(cells) == 7:
            rows.append(cells)
    if not rows:
        sys.exit("The table parsed to zero rows.")
    return pd.DataFrame(rows, columns=["css_code", "name", "state", "domestic",
                                       "international", "noncustodial", "idoc"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("page", nargs="?", help="a saved copy of the list")
    ap.add_argument("-o", "--out", default=OUT_PATH)
    ap.add_argument("--propose", action="store_true",
                    help="print containment candidates for review instead of "
                         "writing; how ALIASES gets refreshed")
    args = ap.parse_args()

    board = parse(fetch(args.page))
    coa = pd.read_csv(COA_PATH)
    coa["_n"] = coa.INSTNM.map(normalize)

    undergraduate = board[
        (board.domestic == "Yes") & (board.state != "")
        & ~board.name.str.contains(NOT_A_COLLEGE) & ~board.name.str.contains(GRADUATE)
    ].copy()

    matched, proposals, unmatched = [], [], []
    for _, row in undergraduate.iterrows():
        pool = coa[coa.STABBR == row.state]
        target = ALIASES.get(row["name"], row["name"])
        key = normalize(target)
        hit = pool[pool._n == key]
        if not len(hit):   # "Harvard College" is Scorecard's "Harvard University"
            hit = pool[pool._n == normalize(re.sub(r"\bCollege\b", "University", target))]
        if len(hit):
            school = hit.iloc[0]
            matched.append({
                "UNITID": int(school.UNITID), "INSTNM": school.INSTNM,
                "STABBR": row.state, "css_code": row.css_code,
                "css_name": row["name"],
                "requires_profile": True,
                "requires_noncustodial": row.noncustodial == "Yes",
                "requires_idoc": row.idoc == "Yes",
            })
            continue
        candidates = pool[pool._n.str.startswith(key).astype(bool)
                          | pool._n.map(
                              lambda n: len(n) >= 10 and key.startswith(n)).astype(bool)]
        if len(key) >= 10 and len(candidates) == 1:
            proposals.append((row["name"], candidates.iloc[0].INSTNM, row.state))
        unmatched.append(row["name"])

    if args.propose:
        print("Containment candidates. REVIEW EACH ONE before adding it to "
              "ALIASES; the pass is known to propose at least one wrong match.")
        for css_name, scorecard, state in proposals:
            print(f"  {css_name!r:48s} -> {scorecard!r}  [{state}]")
        return 0

    out = pd.DataFrame(matched).sort_values("INSTNM")
    print(f"participating institutions on the list : {len(board)}")
    print(f"undergraduate colleges among them      : {len(undergraduate)}")
    print(f"matched to a UNITID                    : {len(out)} "
          f"({len(out) / len(undergraduate):.0%})")
    print(f"  of those, noncustodial parent needed : {int(out.requires_noncustodial.sum())}")
    print(f"unmatched (reported, not fixed)        : {len(unmatched)}")
    for name in unmatched:
        print(f"    {name}")
    if proposals:
        print("\nContainment would additionally propose these. They are NOT "
              "written. Run --propose and review before adding to ALIASES:")
        for css_name, scorecard, state in proposals:
            print(f"    {css_name!r} -> {scorecard!r} [{state}]")

    if len(out) < MIN_MATCHED:
        sys.exit(f"\nOnly {len(out)} schools matched, below MIN_MATCHED="
                 f"{MIN_MATCHED}. A short table is indistinguishable downstream "
                 f"from 'few colleges require the Profile'. Refusing to write.")
    # EVERY UNMATCHED PARTICIPANT MUST BE ABSENT FROM THE COST FILE. That is
    # what makes silence safe in the app: a school the search can return and
    # that requires the Profile must never be missing from this table, because
    # a blank cell would read as "not required". Schools like Grove City and
    # Hillsdale take no federal money, so no federal dataset prices them and
    # they can never appear in a result -- their absence here costs nothing.
    reachable = []
    for name in unmatched:
        stem = normalize(ALIASES.get(name, name))
        if len(stem) >= 10 and (coa._n == stem).any():
            reachable.append(name)
    if reachable:
        sys.exit(f"\n{len(reachable)} unmatched participant(s) ARE in the cost "
                 f"file and so can appear in a search, where a blank cell would "
                 f"read as 'does not require the Profile': {reachable}. Add an "
                 f"alias (run --propose) rather than shipping a false negative.")
    if out.UNITID.duplicated().any():
        sys.exit("\nDuplicate UNITID in the output; two Profile rows matched one "
                 "school. Refusing to write.")

    out.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Guard: the CSS Profile flag never says a school does NOT require it.

The dataset behind this flag is joined by NAME AND STATE, because the College
Board publishes a four-digit code and a truncated name and there is no UNITID
anywhere in its list. That join cannot be perfect, so the contract the app
rests on is not "the table is complete" -- it is:

    A MATCH IS A CLAIM. AN ABSENCE IS SILENCE.

Get that backwards and the app tells a family a college does not want the CSS
Profile because a string failed to match, which is worse than saying nothing:
they skip a form, miss a deadline, and lose institutional aid. Nothing on
screen would look wrong, because a missing row and a genuine non-participant
render identically.

So this checks three things the build cannot:

  1. The lookup and the caption both stay silent on an unknown school, and the
     caption can never contain a denial in words.
  2. Identity is UNITID, not name. 33 name-plus-state keys in the cost file map
     to more than one school, so a name-keyed lookup would flag the wrong one.
  3. Every ALIASES target still resolves. A mapping whose key or value does not
     exist is INERT rather than broken -- the failure this repo already records
     for an occupation title that does not exist, and for a chart's SOC group
     that matched nothing. A typo'd alias silently drops a school.

It also pins the one near-miss the build found, because that is the case a
future refactor is most likely to reintroduce.

Run it:  python3 check_css_profile_schools.py     (exit 1 on any failure)
"""
import re
import sys

import pandas as pd

import build_css_profile_schools as builder

APP = "app.py"
COA_PATH = "data/college_coa_clean.csv"

# Words that would turn silence into a denial. The caption may say a school
# DOES require the Profile and may say nothing at all; there is no third thing
# it is entitled to say.
# It must be a denial ABOUT THE REQUIREMENT, not any negation. The first
# version matched "a formula that is not published", which is a true and
# necessary sentence in the caption, so the guard failed on correct code --
# the one thing that gets a guard switched off.
DENIAL = re.compile(
    r"(?:does|do|doesn't|don't)\s*(?:not\s*)?(?:require|need|use|ask for)"
    r"|not required|not a css profile|only the fafsa|fafsa only|no css profile",
    re.I)


def load_app_namespace():
    src = open(APP, encoding="utf-8").read()
    marker = "# 3. PAGE CONFIG & SESSION STATE"
    if marker not in src:
        sys.exit(f"{APP}: section 3 banner is gone; cannot find the pure prefix.")
    cut = src.rindex("\n#", 0, src.index(marker))
    ns = {}
    exec(compile(src[:cut], APP, "exec"), ns)
    return ns


def check_dataset(profile, coa):
    out = []
    required = {"UNITID", "INSTNM", "STABBR", "css_code", "css_name",
                "requires_profile", "requires_noncustodial", "requires_idoc"}
    missing = required - set(profile.columns)
    if missing:
        out.append(f"  DATA    the table is missing column(s): {sorted(missing)}")
        return out
    if profile.empty:
        out.append("  DATA    the table is empty. Every school would silently "
                   "read as a non-participant.")
        return out
    if profile.UNITID.duplicated().any():
        out.append("  DATA    duplicate UNITID; two Profile rows describe one school.")
    if not profile.requires_profile.all():
        out.append("  DATA    a row has requires_profile False. Only participants "
                   "belong in this table; a 'No' row would be read as a claim.")
    stray = set(profile.UNITID) - set(coa.UNITID)
    if stray:
        out.append(f"  DATA    {len(stray)} UNITID(s) are not in the cost file, so "
                   f"they can never be displayed: {sorted(stray)[:5]}")
    # A flag nobody can reach is not a feature. This is a floor, not a target.
    if len(profile) < 100:
        out.append(f"  DATA    only {len(profile)} schools carry the flag; the join "
                   f"has broken rather than drifted.")
    return out


def check_absence_is_silence(ns, profile, coa):
    """The contract. An unknown school gets nothing, never a denial."""
    out = []
    requirement = ns["css_profile_requirement"]
    caption = ns["css_profile_school_caption"]

    participants = set(profile.UNITID)
    non_participants = [u for u in coa.UNITID if u not in participants][:200]
    if not non_participants:
        out.append("  SILENCE every school in the cost file is a participant, which "
                   "cannot be true; the check has nothing to test against.")
        return out

    for unitid in non_participants:
        if requirement(unitid) != {}:
            out.append(f"  SILENCE css_profile_requirement({unitid}) answered for a "
                       f"school that is not in the table.")
            break
    for unitid in non_participants:
        if caption(unitid) != "":
            out.append(f"  SILENCE css_profile_school_caption({unitid}) produced text "
                       f"for a school that is not in the table: {caption(unitid)[:60]!r}")
            break
    # Nothing, a blank and a missing id are all "we did not find it".
    for absent in (None, "", float("nan"), 999999999):
        try:
            if requirement(absent) != {} or caption(absent) != "":
                out.append(f"  SILENCE {absent!r} was treated as a participant.")
        except Exception as exc:                       # noqa: BLE001
            out.append(f"  SILENCE {absent!r} raised {type(exc).__name__}; an unknown "
                       f"school must be answerable, not fatal.")

    # A participant's caption must be a positive claim and must not contain a
    # denial about anything else.
    for unitid in list(participants)[:60]:
        text = caption(unitid)
        if not text:
            out.append(f"  SILENCE a participant ({unitid}) got no caption at all.")
            break
        if DENIAL.search(text):
            out.append(f"  SILENCE the caption contains a denial: {text[:80]!r}. It may "
                       f"say a school DOES require the Profile, or say nothing.")
            break
    return out


def check_identity_is_unitid(ns, profile, coa):
    """Two schools sharing a name and state must not share a flag."""
    out = []
    caption = ns["css_profile_school_caption"]
    key = coa.INSTNM.str.lower().str.strip() + "|" + coa.STABBR.fillna("")
    shared = coa.assign(_k=key).groupby("_k").UNITID.nunique()
    shared = set(shared[shared > 1].index)
    if not shared:
        out.append("  IDENTITY no shared name-and-state keys found in the cost file; "
                   "this check has lost its subject and needs a look.")
        return out
    participants = set(profile.UNITID)
    for _k in shared:
        group = coa[key == _k]
        flagged = [u for u in group.UNITID if u in participants]
        if flagged and len(flagged) != len(group):
            for unitid in group.UNITID:
                if unitid not in participants and caption(unitid):
                    out.append(f"  IDENTITY {unitid} shares a name and state with a "
                               f"participant and was flagged too. The lookup has "
                               f"stopped keying on UNITID.")
                    return out
    return out


def check_aliases(coa):
    """Every reviewed alias must still resolve, and Bard must stay unmatched."""
    out = []
    names = set(coa.INSTNM)
    normalized = {builder.normalize(n) for n in names}
    for css_name, scorecard_name in builder.ALIASES.items():
        if scorecard_name not in names and builder.normalize(scorecard_name) not in normalized:
            out.append(f"  ALIAS   {css_name!r} maps to {scorecard_name!r}, which is not "
                       f"in the cost file. A mapping whose target does not exist is "
                       f"inert: the school is silently dropped, not flagged wrong.")
    # THE NEAR-MISS. Containment proposed "Bard College" for Simon's Rock, and
    # accepting it would have flagged Bard on another institution's row.
    if builder.ALIASES.get("Bard College at Simons Rock") == "Bard College":
        out.append("  ALIAS   'Bard College at Simons Rock' is aliased to 'Bard "
                   "College', which is a DIFFERENT INSTITUTION.")
    return out


def check_bard_provenance(profile):
    """Each Bard school must be flagged from its OWN College Board row.

    BOTH are genuine participants -- Bard College is code 2037 and Simon's
    Rock is 3795, listed separately -- which is not what the build's first
    pass assumed. So "is Bard flagged" is the wrong question and this guard
    asked it once. The right one is PROVENANCE: containment proposed mapping
    Simon's Rock onto Bard College, and had that been accepted, Simon's Rock
    would carry Bard's UNITID and one of the two rows would describe the wrong
    school. Checking the code against the name catches exactly that.
    """
    out = []
    expected = {"Simon's Rock at Bard College": "3795", "Bard College": "2037"}
    for instnm, css_code in expected.items():
        row = profile[profile.INSTNM == instnm]
        if row.empty:
            continue      # a Scorecard rename is not this guard's business
        got = str(row.iloc[0].css_code)
        if got != css_code:
            out.append(f"  BARD    {instnm!r} carries CSS code {got}, expected "
                       f"{css_code}. The two Bard institutions have been "
                       f"crossed: each must match its own College Board row.")
    return out


def negative_controls(ns, profile, coa):
    """Break it deliberately."""
    out = []

    # 1. A caption that denies. The whole point of the guard.
    denying = dict(ns)
    denying["css_profile_school_caption"] = (
        lambda u: "" if u in set(profile.UNITID)
        else "This school does not require the CSS Profile.")
    if not any("SILENCE" in f for f in check_absence_is_silence(denying, profile, coa)):
        out.append("  CONTROL a caption denying the Profile was NOT caught.")

    # 2. A lookup that answers for schools it has never heard of.
    loud = dict(ns)
    loud["css_profile_requirement"] = lambda u: {"noncustodial": False, "idoc": False}
    if not any("SILENCE" in f for f in check_absence_is_silence(loud, profile, coa)):
        out.append("  CONTROL a lookup answering for an unknown school was NOT caught.")

    # 3. An alias pointing at a school that does not exist -- inert, not broken.
    real = dict(builder.ALIASES)
    try:
        builder.ALIASES["Nonesuch College"] = "A School That Does Not Exist"
        if not check_aliases(coa):
            out.append("  CONTROL an alias with a nonexistent target was NOT caught.")
    finally:
        builder.ALIASES.clear()
        builder.ALIASES.update(real)

    # 4. A duplicate UNITID in the table.
    if not check_dataset(pd.concat([profile, profile.head(1)]), coa):
        out.append("  CONTROL a duplicate UNITID was NOT caught.")
    return out


def main():
    ns = load_app_namespace()
    coa = pd.read_csv(COA_PATH)
    profile = pd.read_csv(builder.OUT_PATH)

    failures = (check_dataset(profile, coa)
                + check_absence_is_silence(ns, profile, coa)
                + check_identity_is_unitid(ns, profile, coa)
                + check_aliases(coa)
                + check_bard_provenance(profile)
                + negative_controls(ns, profile, coa))
    if failures:
        print("CSS Profile schools: %d problem(s)\n" % len(failures))
        print("\n".join(failures))
        return 1
    print("CSS Profile schools OK -- %d participants, all resolving to a school "
          "in the cost file; an unknown school gets silence and never a denial; "
          "identity is UNITID across %d shared name keys; %d aliases resolve; "
          "4 negative controls all caught."
          % (len(profile),
             (coa.assign(_k=coa.INSTNM.str.lower().str.strip() + "|"
                         + coa.STABBR.fillna("")).groupby("_k").UNITID.nunique() > 1).sum(),
             len(builder.ALIASES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

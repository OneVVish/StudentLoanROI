"""
Standalone research-analysis script for the Student Loan Payoff & Major
ROI Calculator's survey_responses table.

This is NOT part of the deployed Streamlit app -- app.py never imports it,
and it never runs on Streamlit Cloud. It's a local tool for digging into
the same data the in-app Admin Analytics Dashboard (?admin=1) summarizes,
aimed at the app's actual research question: does exposure to real
loan/ROI numbers change how people report viewing their major/school
choice, and if so, for whom and under what financial profile?

Usage:
    python3 analyze_survey.py

Reads the same .streamlit/secrets.toml credentials the app itself uses
(see secrets.toml.example), and talks to Supabase directly via the plain
supabase-py client -- no Streamlit runtime needed.

Optional: `pip install matplotlib` to also save a couple of bar charts
under analysis_output/. Everything else (the printed cross-tabs) works
with just pandas + supabase-py, which the app already depends on.

The paired pre/post analysis follows the same rule. Its inferential test is
an EXACT SIGN TEST written in pure Python (math.comb), so it always runs --
scipy is not in requirements.txt, and adding it would put a dependency in the
deploy for a script the app never imports. Where scipy happens to be
installed, a Wilcoxon signed-rank is printed alongside as a supplement.

The sign test is not a compromise here. These are 6-7 category ordinal bands,
so the rank magnitudes Wilcoxon relies on are not really measured -- the
distance from "$10-30k" to "$30-60k" is not a known quantity. Counting the
direction of movement is the claim the data actually supports.
"""

import math
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

import pandas as pd
from supabase import create_client

SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"
OUTPUT_DIR = Path(__file__).parent / "analysis_output"

# Matches the exact order the survey form presents these in (app.py's
# perception_change radio) so cross-tabs read "most changed -> least
# changed" instead of pandas' default alphabetical order.
PERCEPTION_ORDER = ["Yes - significantly", "Yes - slightly", "No - it confirmed my choice", "No - no impact"]

# A small, local stopword list (no NLTK download needed) for the feedback
# text word-frequency pass -- not exhaustive, just enough to surface
# actual content words instead of "the/and/was" noise.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "it", "to", "of", "in", "for",
    "on", "that", "this", "was", "with", "as", "at", "by", "be", "i", "my",
    "me", "we", "our", "you", "your", "so", "if", "not", "no", "yes", "very",
    "really", "just", "about", "how", "what", "will", "would", "could",
    "did", "do", "does", "are", "have", "has", "had", "than", "more", "much",
    "think", "know", "were", "am", "its", "it's", "also", "get", "out",
}


def load_supabase_client():
    if not SECRETS_PATH.exists():
        sys.exit(f"No secrets found at {SECRETS_PATH} -- copy secrets.toml.example there and fill in real values.")
    with open(SECRETS_PATH, "rb") as f:
        secrets = tomllib.load(f)
    conn = secrets["connections"]["supabase_connection"]
    return create_client(conn["SUPABASE_URL"], conn["SUPABASE_KEY"])


def fetch_table(client, table_name: str) -> pd.DataFrame:
    """Fetch a table, automatically excluding developer/test rows.

    The app's ?test=1 flag skips Supabase logging entirely, so most test
    sessions never reach the database. Rows logged *before* that flag existed
    are marked instead, via a backfilled boolean `is_test` column (add it once
    in the Supabase SQL editor:
        ALTER TABLE <table> ADD COLUMN IF NOT EXISTS is_test boolean NOT NULL DEFAULT false;
        UPDATE <table> SET is_test = true;   -- run before any real traffic
    ). If that column is present, rows flagged true are dropped here so every
    analysis is clean without remembering to filter. If it isn't there yet,
    this falls back to using all rows unchanged. false / NULL / missing all
    count as real data -- only an explicit true is treated as a test row.
    """
    response = client.table(table_name).select("*").execute()
    df = pd.DataFrame(response.data)
    if "is_test" in df.columns:
        is_test = df["is_test"].fillna(False).astype(bool)
        dropped = int(is_test.sum())
        if dropped:
            df = df[~is_test].reset_index(drop=True)
            print(f"  ({table_name}: excluded {dropped} test row(s) flagged is_test=true)",
                  file=sys.stderr)
    return df


def bucket_dti(dti):
    if pd.isna(dti):
        return None
    if dti < 0.5:
        return "<0.5x"
    if dti < 1.0:
        return "0.5-1x"
    if dti < 1.5:
        return "1-1.5x"
    if dti < 2.0:
        return "1.5-2x"
    return "2x+"


def bucket_roi(roi_pct):
    if pd.isna(roi_pct):
        return None
    if roi_pct < 0:
        return "Negative"
    if roi_pct < 50:
        return "0-50%"
    if roi_pct < 100:
        return "50-100%"
    if roi_pct < 200:
        return "100-200%"
    return "200%+"


# The optional Advanced Analysis modules, as (flag_column, label). Each flag
# is NULL unless that module was switched on at save-time, so notna() is the
# "was this module used" test.
MODULE_FLAGS = [
    ("prestige_mode_active", "College Prestige"),
    ("ai_mode_active", "AI Employability Risk"),
    ("future_forecasting_active", "2026 Regulatory Forecasting"),
    ("apprenticeship_active", "Trade Apprenticeship"),
]

# The apprenticeship_* columns were missing from the tables until the
# 2026-07-15 migration, and PostgREST rejects the whole row on an unknown
# column -- so any session that switched that module on had its save dropped
# entirely and never appears here at all, rather than appearing with blank
# fields. Rows written after the migration are unaffected. This caveat is
# about the historical data only; delete it once pre-migration rows are no
# longer part of whatever's being analyzed (they're identifiable by
# session_id IS NULL, which is also when session_id shipped).
ENGAGEMENT_CAVEAT = (
    "CAVEAT: rows predating the 2026-07-15 migration (session_id IS NULL) are\n"
    "  a sample biased toward 'Trade Apprenticeship off' -- sessions using that\n"
    "  module had their inserts rejected outright and are absent, not blank.\n"
    "  Apprenticeship adoption among those rows reads as zero regardless of\n"
    "  what actually happened. Rows with a session_id are unaffected."
)


# ---- Paired pre/post instrument --------------------------------------------
# Codes mirror app.py's PRESURVEY_* option maps. They are duplicated here
# rather than imported because this script deliberately does not import app.py
# (that would execute the whole Streamlit page). Keep them in step: the app
# writes these strings, and a code missing from a map below silently drops
# that respondent out of the paired analysis rather than erroring.
SCHOOLS_INDEX = {"s0": 0, "s1": 1, "s2": 2, "s3": 3, "s4": 4, "s5plus": 5}
BORROW_INDEX = {"n0": 0, "b1": 1, "b2": 2, "b3": 3, "b4": 4, "b5": 5}

# Responses that are NOT missing data and must never be dropna()'d away. Each
# is a finding in its own right -- "haven't decided" before seeing any numbers
# is the paper's information-asymmetry thesis in one click -- so they are
# counted, reported, and only then excluded from the arithmetic BY NAME.
NON_NUMERIC_RESPONSES = {"unsure", "undecided", "skip", "n_a"}


def exact_sign_test(negative: int, positive: int) -> float:
    """Two-sided exact sign test p-value for a paired shift.

    Ties are discarded, which is the test's definition, not an oversight: a
    respondent who did not move contributes no evidence about direction.

    Pure Python so this always runs. scipy is not in requirements.txt, and the
    deployed app must not gain a dependency for a script it never imports.
    """
    n = negative + positive
    if n == 0:
        return float("nan")
    k = min(negative, positive)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def parse_presurvey_events(usage_df: pd.DataFrame) -> pd.DataFrame:
    """One row per session that saw the pre block, from usage_logs.action.

    This table is the ONLY record of a session that answered the pre and never
    reached the survey -- which is most of them, and the entire basis for
    measuring drop-off. The survey row carries its own copy for sessions that
    did finish; that copy is what makes a pair atomic.
    """
    if usage_df.empty or "action" not in usage_df.columns:
        return pd.DataFrame()
    actions = usage_df[usage_df["action"].astype(str).str.startswith(
        ("presurvey_", "postsurvey_", "survey_blocked_"))]
    if actions.empty:
        return pd.DataFrame()

    rows = []
    for record in actions.itertuples():
        action = str(record.action)
        name = action.split(":", 1)[0]
        fields = dict(part.split("=", 1) for part in action.split(":")[1:] if "=" in part)
        rows.append({"session_id": getattr(record, "session_id", None),
                     "event": name, **fields})
    return pd.DataFrame(rows)


def build_paired_frame(survey_df: pd.DataFrame) -> pd.DataFrame:
    """Survey rows with the paired shift computed, and every row labelled.

    `pair_status` is the point of this function. Partial pairs are reported,
    never silently dropped: a respondent who skipped the pre is a different
    fact from one who predates the instrument, and both are different from one
    who answered and moved zero bands.
    """
    df = survey_df.copy()
    for column in ("pre_schools_considered", "pre_borrow_willingness",
                    "post_schools_considered", "post_borrow_willingness",
                    "pre_skipped", "instrument_version"):
        if column not in df.columns:
            df[column] = None

    def status(row):
        if pd.isna(row["instrument_version"]):
            return "pre_instrument_era"      # collected before the pre existed
        if row.get("pre_skipped") is True:
            return "declined_pre"
        if pd.isna(row["pre_schools_considered"]):
            return "pre_not_shown"
        return "paired"

    df["pair_status"] = df.apply(status, axis=1)

    def shift(row, pre_col, post_col, index_map):
        pre, post = row[pre_col], row[post_col]
        if pre in NON_NUMERIC_RESPONSES or post in NON_NUMERIC_RESPONSES:
            return None
        if pre in index_map and post in index_map:
            return index_map[post] - index_map[pre]
        return None

    df["schools_shift"] = df.apply(
        shift, axis=1, args=("pre_schools_considered", "post_schools_considered", SCHOOLS_INDEX))
    df["borrow_shift"] = df.apply(
        shift, axis=1, args=("pre_borrow_willingness", "post_borrow_willingness", BORROW_INDEX))
    return df


def _report_shift(df: pd.DataFrame, column: str, label: str, index_map: dict,
                   pre_col: str, post_col: str):
    """Direction counts, exact sign test, and the full transition matrix."""
    values = df[column].dropna()
    if values.empty:
        print(f"\n  {label}: no complete pairs yet.")
        return
    down = int((values < 0).sum())
    same = int((values == 0).sum())
    up = int((values > 0).sum())
    p = exact_sign_test(down, up)
    print(f"\n  {label}  (n = {len(values)} complete pairs)")
    print(f"    moved down {down} | unchanged {same} | moved up {up}")
    print(f"    median shift {values.median():+.1f} bands | exact sign test p = {p:.4f}")
    try:                                   # supplement only where available
        from scipy.stats import wilcoxon
        if down + up:
            print(f"    Wilcoxon signed-rank p = {wilcoxon(values).pvalue:.4f}")
    except Exception:
        pass

    # The marginals are where floor and ceiling effects show; a mean alone
    # hides that everyone at the top band could only move one way.
    order = list(index_map) + sorted(NON_NUMERIC_RESPONSES)
    matrix = pd.crosstab(df[pre_col], df[post_col]).reindex(
        index=order, columns=order).dropna(how="all").dropna(axis=1, how="all")
    if not matrix.empty:
        print("\n    pre (rows) -> post (columns):")
        print(matrix.fillna(0).astype(int).to_string().replace("\n", "\n      "))


def analyze_paired_shift(survey_df: pd.DataFrame, usage_df: pd.DataFrame):
    """The measured change the retrospective item could only ask about."""
    print_section(
        "PAIRED PRE/POST SHIFT",
        "Same two questions before the numbers and after -- differenced, not recalled.",
    )
    if survey_df.empty:
        print("  (no survey responses yet)")
        return

    df = build_paired_frame(survey_df)
    print("  Response composition (every row accounted for):")
    for status, count in df["pair_status"].value_counts().items():
        print(f"    {status:20s} {count}")

    paired = df[df["pair_status"] == "paired"]
    _report_shift(paired, "schools_shift", "Colleges being considered", SCHOOLS_INDEX,
                   "pre_schools_considered", "post_schools_considered")
    _report_shift(paired, "borrow_shift", "Willingness to borrow", BORROW_INDEX,
                   "pre_borrow_willingness", "post_borrow_willingness")

    # Non-numeric answers reported rather than quietly excluded.
    for column, label in (("pre_borrow_willingness", "pre"), ("post_borrow_willingness", "post")):
        counts = df[df[column].isin(NON_NUMERIC_RESPONSES)][column].value_counts()
        if not counts.empty:
            print(f"\n  Non-numeric {label} borrowing answers (excluded from the shift, "
                  f"counted here):")
            for value, count in counts.items():
                print(f"    {value:12s} {count}")

    _report_presurvey_funnel(usage_df)


def _report_presurvey_funnel(usage_df: pd.DataFrame):
    """Who saw the pre block, who answered, and who fell out before the survey."""
    events = parse_presurvey_events(usage_df)
    if events.empty:
        print("\n  (no pre-block events in usage_logs yet)")
        return
    counts = events["event"].value_counts()
    print("\n  Pre-block funnel:")
    for event in ("presurvey_shown", "presurvey_answered", "presurvey_skipped",
                   "presurvey_ineligible_minor", "postsurvey_answered",
                   "survey_blocked_minor"):
        print(f"    {event:28s} {int(counts.get(event, 0))}")
    shown = int(counts.get("presurvey_shown", 0))
    answered = int(counts.get("presurvey_answered", 0))
    if shown:
        print(f"    -> answered {answered / shown:.0%} of those shown")
    if answered:
        reached = int(counts.get("postsurvey_answered", 0))
        print(f"    -> of those, {reached / answered:.0%} went on to submit the survey")
        print("       (the gap is the drop-off the pre exists to make measurable)")


def analyze_instrument_agreement(survey_df: pd.DataFrame):
    """Does the retrospective self-report agree with the measured shift?

    If respondents who say "no impact" moved a band -- or those who say
    "significantly" did not -- that is a finding about the instrument the
    paper's headline currently rests on, and it is only visible with both
    measures present.
    """
    print_section(
        "SELF-REPORT vs MEASURED SHIFT",
        "Validation of perception_change against what the paired answers actually did.",
    )
    df = build_paired_frame(survey_df)
    paired = df[(df["pair_status"] == "paired") & df["borrow_shift"].notna()]
    if paired.empty:
        print("  (needs complete pairs -- none yet)")
        return
    paired = paired.copy()
    paired["moved"] = paired["borrow_shift"].apply(
        lambda v: "moved down" if v < 0 else ("moved up" if v > 0 else "no change"))
    table = pd.crosstab(paired["perception_change"], paired["moved"])
    print(table.to_string())
    print("\n  Read the off-diagonal: 'Yes - significantly' with no change, or")
    print("  'No - no impact' with a band move, are both disagreements between")
    print("  what respondents say happened and what they did.")


def print_section(title: str, subtitle: str = ""):
    print("\n" + "=" * 78)
    print(title)
    if subtitle:
        print(subtitle)
    print("=" * 78)


def crosstab_pct(df: pd.DataFrame, index_col: str, perception_col: str = "perception_change"):
    """Row-normalized cross-tab against perception_change (each row sums to
    100%), with a Count column and columns ordered Yes-significantly ->
    No-no-impact. Prints a message instead of crashing on too little data."""
    valid = df.dropna(subset=[index_col, perception_col])
    if valid.empty:
        print("  (not enough data yet)")
        return
    counts = valid.groupby(index_col, observed=True).size().rename("Count")
    ct = pd.crosstab(valid[index_col], valid[perception_col], normalize="index") * 100
    ct = ct.reindex(columns=[c for c in PERCEPTION_ORDER if c in ct.columns]).round(1)
    ct.insert(0, "Count", counts)
    print(ct.to_string())


def build_engagement_frame(pdf_df: pd.DataFrame, shares_df: pd.DataFrame) -> pd.DataFrame:
    """pdf_downloads + scenario_shares unioned into one frame with an
    event_type column. Both tables store the identical scenario-context shape
    (see build_scenario_context in app.py -- pdf_downloads and scenario_shares
    are the same columns as survey_responses minus the four respondent
    fields), so they concat cleanly and every cross-tab below works across
    both at once while still splitting by event_type where that matters.

    These are the app's two "commitment" actions: bothering to download a
    report or share a link is a stronger signal than a pageview, and together
    they carry several times more scenario rows than the survey does.

    Note there is no session/user id on any table, so these events cannot be
    joined to survey_responses -- an engagement event and a survey response
    from the same visitor are unlinkable. Everything here describes what gets
    modeled, not who modeled it.
    """
    frames = []
    for df, event_type in [(pdf_df, "pdf_download"), (shares_df, "scenario_share")]:
        if df.empty:
            continue
        tagged = df.copy()
        tagged["event_type"] = event_type
        frames.append(tagged)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def analyze_engagement(events: pd.DataFrame):
    """Everything the combined pdf_downloads + scenario_shares data can
    actually answer: what scenarios people commit to, at what financial
    profile, with which optional modules on."""
    print_section(
        "ENGAGEMENT EVENTS: PDF DOWNLOADS + SCENARIO SHARES",
        "What scenarios do people care enough about to take away or send on?",
    )
    if events.empty:
        print("  (no engagement events yet)")
        return
    print(f"Total engagement events: {len(events)}")
    print(events["event_type"].value_counts().to_string())
    print("\n" + ENGAGEMENT_CAVEAT)

    print_section("ENGAGEMENT: TOP MAJORS MODELED")
    print(events["scenario_a_major"].value_counts().head(10).to_string())

    print_section("ENGAGEMENT: TOP SCHOOLS MODELED")
    schools = events["scenario_a_school_name"].dropna()
    print(schools.value_counts().head(10).to_string() if not schools.empty else "  (none entered)")

    print_section(
        "ENGAGEMENT: FINANCIAL PROFILE OF MODELED SCENARIOS",
        "Are people taking away good-news scenarios or bad-news ones?",
    )
    events = events.copy()
    events["dti_bucket"] = events["scenario_a_dti_ratio"].apply(bucket_dti)
    events["roi_bucket"] = events["scenario_a_roi_pct"].apply(bucket_roi)
    for col, order, label in [
        ("dti_bucket", ["<0.5x", "0.5-1x", "1-1.5x", "1.5-2x", "2x+"], "Debt-to-income ratio"),
        ("roi_bucket", ["Negative", "0-50%", "50-100%", "100-200%", "200%+"], "10-year ROI %"),
    ]:
        counts = events[col].value_counts().reindex(order).dropna()
        print(f"\n{label}:")
        print(counts.to_string() if not counts.empty else "  (not enough data yet)")
    for col, label in [("scenario_a_roi_pct", "ROI %"), ("scenario_a_dti_ratio", "DTI ratio"),
                       ("scenario_a_loan_amount", "Loan amount")]:
        vals = pd.to_numeric(events[col], errors="coerce").dropna()
        if not vals.empty:
            print(f"\n{label}: median {vals.median():,.2f} (min {vals.min():,.2f}, max {vals.max():,.2f})")

    print_section(
        "ENGAGEMENT: COMPARE MODE USE, BY EVENT TYPE",
        "Do people who compare two scenarios commit differently than single-scenario users?",
    )
    events["used_compare_mode"] = events["scenario_b_major"].notna()
    ct = pd.crosstab(events["event_type"], events["used_compare_mode"])
    print(ct.to_string())
    compare_users = events[events["used_compare_mode"]]
    if not compare_users.empty and compare_users["roi_pct_delta"].notna().any():
        deltas = pd.to_numeric(compare_users["roi_pct_delta"], errors="coerce").dropna()
        print(f"\nAmong Compare Mode engagement events, average |ROI % delta|: "
              f"{deltas.mean():.1f} points (median {deltas.median():.1f}, n={len(deltas)})")

    print_section(
        "ENGAGEMENT: OPTIONAL MODULE ADOPTION",
        "Which Advanced Analysis modules are people actually turning on?",
    )
    rows = []
    for flag, label in MODULE_FLAGS:
        if flag not in events.columns:
            continue
        used = events[flag].notna()
        rows.append({
            "Module": label,
            "Events": int(used.sum()),
            "% of events": round(used.mean() * 100, 1),
        })
    print(pd.DataFrame(rows).to_string(index=False) if rows else "  (no module columns present)")


def analyze_switch_rate(events_df: pd.DataFrame):
    """Table 4: per-major switch rate -- how often a major, once its DTI was
    on screen, got abandoned for something else within the same session.

    Reads scenario_events, which records one row per distinct major/school a
    session lands on (see maybe_log_scenario_event in app.py). A "switch" is
    a session where a major appears at some event_seq and a *different* major
    appears at a later one. Ordered by event_seq, never timestamp -- those
    come from the visitor's clock and can tie.

    Two things make this weaker than it looks, and both belong in the paper
    rather than in a footnote:

    1. The sidebar lands pre-filled, so a major the visitor never chose is
       still recorded. major_explicitly_selected separates the two, and rows
       where it's false are dropped below: false means "we don't know",
       not "they agreed with the default", and counting them as Software
       Developers would manufacture a finding out of the app's own default.
       Rows predating that flag are NULL and are dropped for the same reason.

    2. The last major in a session can never be recorded as switched away
       from, since nothing follows it. Sessions with a single event
       contribute a denominator and no possible switch.
    """
    print_section(
        "SWITCH RATE BY MAJOR (paper Table 4)",
        "Once a major's DTI was visible, how often was it abandoned in-session?",
    )
    if events_df.empty:
        print("  (no scenario_events yet -- this needs post-deploy traffic)")
        return
    if "event_seq" not in events_df.columns or events_df["event_seq"].isna().all():
        print("  (scenario_events has no event_seq -- migration incomplete?)")
        return

    all_events = events_df.dropna(subset=["session_id", "scenario_a_major"]).copy()
    all_events = all_events.sort_values(["session_id", "event_seq"])

    # Keep only majors the visitor actually picked -- see caveat 1 above.
    if "major_explicitly_selected" in all_events.columns:
        df = all_events[all_events.major_explicitly_selected == True]  # noqa: E712
    else:
        print("  (no major_explicitly_selected column -- migration incomplete?)")
        return
    dropped = len(all_events) - len(df)
    print(f"Sessions with a recorded path: {df.session_id.nunique()}  "
          f"(events: {len(df)})")
    if dropped:
        print(f"Excluded {dropped} event(s) where the major was the app's default, "
              f"not the visitor's pick.")
    if df.empty:
        print("  (every recorded event was a landing default -- nothing to rank. "
              "This is what the data looks like when nobody engaged the dropdown.)")
        return

    multi = df.groupby("session_id").filter(lambda g: g.scenario_a_major.nunique() > 1)
    print(f"Sessions that tried more than one major: {multi.session_id.nunique()}")
    if multi.empty:
        print("  (nobody has switched majors yet -- nothing to rank)")
        return

    rows = []
    for major, seen in df.groupby("scenario_a_major"):
        sessions_seen = set(seen.session_id)
        switched = set()
        for sid in sessions_seen:
            path = df[df.session_id == sid]
            first_at = path[path.scenario_a_major == major].event_seq.min()
            later = path[path.event_seq > first_at]
            if not later.empty and (later.scenario_a_major != major).any():
                switched.add(sid)
        rows.append({
            "major": major,
            "sessions_seen": len(sessions_seen),
            "sessions_switched_away": len(switched),
            "switch_rate_pct": round(len(switched) / len(sessions_seen) * 100, 1),
            "median_dti_when_seen": round(pd.to_numeric(seen.scenario_a_dti_ratio,
                                                        errors="coerce").median(), 3),
        })

    table = pd.DataFrame(rows).sort_values("median_dti_when_seen", ascending=False)
    print("\nRanked by DTI when seen (visitor-selected majors only):")
    print(table.to_string(index=False))
    print("\n  A major that is only ever a session's LAST selection shows 0% by\n"
          "  construction -- nothing follows it to switch to. Read alongside\n"
          "  sessions_seen; a 0% on n=1 is not a result.")


def parse_school_search_events(usage_df: pd.DataFrame) -> pd.DataFrame:
    """One row per school-search action, from usage_logs.action.

    Same shape as parse_presurvey_events: the action string is a colon-joined
    name plus key=value pairs, and everything after the name is a field.
    """
    if usage_df.empty or "action" not in usage_df.columns:
        return pd.DataFrame()
    actions = usage_df[usage_df["action"].astype(str).str.startswith("school_search_")]
    if actions.empty:
        return pd.DataFrame()
    rows = []
    for record in actions.itertuples():
        action = str(record.action)
        name = action.split(":", 1)[0]
        fields = dict(part.split("=", 1) for part in action.split(":")[1:] if "=" in part)
        rows.append({"session_id": getattr(record, "session_id", None),
                      "timestamp": getattr(record, "timestamp", None),
                      "event": name, **fields})
    return pd.DataFrame(rows)


def analyze_school_search(usage_df: pd.DataFrame):
    """Does the budget-first search actually move anyone to a cheaper school?

    This is the inverse-search feature's whole claim, and it needs no column
    the app doesn't already write: school_search_apply carries the school the
    visitor switched TO, the one they were already modelling, and the
    difference. A negative delta is the feature working.

    Reported as a funnel, because the interesting failures are at the joins:
    a search that returns nothing is a real answer ("your budget admits nothing
    in this field") and is logged deliberately, so a high zero-result rate is a
    finding about budgets, not a bug. A search that returns results and is
    never applied is the other failure -- the list was not persuasive.

    Deliberately NOT joined to scenario_events here. The apply already records
    the before and after COA at click time, which is stronger than inferring
    the switch from the next scenario row: _apply_pending_school overwrites the
    previous value on the very next rerun, so a join could only ever see the
    after.
    """
    print_section(
        "BUDGET-FIRST SCHOOL SEARCH",
        "Did surfacing cheaper schools change which one gets modelled?",
    )
    events = parse_school_search_events(usage_df)
    if events.empty:
        print("  (no school-search events yet -- this needs post-deploy traffic)")
        return

    runs = events[events["event"] == "school_search_run"]
    applies = events[events["event"] == "school_search_apply"]
    searchers = set(runs["session_id"].dropna())
    appliers = set(applies["session_id"].dropna())
    # Numerator is sessions in BOTH sets, not every applier. An apply whose run
    # row is missing (a dropped insert) would otherwise push the rate above
    # 100%, which reads as a broken report rather than as the missing row it is.
    converted = searchers & appliers

    print(f"  sessions that ran a search : {len(searchers)}")
    print(f"  sessions that applied one  : {len(converted)}"
          + (f"  ({len(converted) / len(searchers):.0%} of searchers)"
             if searchers else ""))
    if appliers - searchers:
        print(f"  ({len(appliers - searchers)} apply(s) with no run row -- "
              f"the run insert did not land; excluded from the rate)")

    if "n" in runs.columns:
        counts = pd.to_numeric(runs["n"], errors="coerce")
        zero = int((counts == 0).sum())
        print(f"  searches returning nothing : {zero} of {len(runs)}"
              f"  ({zero / len(runs):.0%})" if len(runs) else "")

    if "level" in runs.columns:
        by_level = runs["level"].value_counts()
        print("\n  searches by credential level:")
        for level, n in by_level.items():
            print(f"    {level:>10}  {n}")
        if "unset" in by_level.index:
            print("    ('unset' predates the level= field -- not a bachelor's)")

    if applies.empty:
        print("\n  No applies yet, so no effect size. A search that returns\n"
              "  results and is never applied means the list did not persuade;\n"
              "  that is a finding, not missing data.")
        return

    # Column-presence check, not applies.get(): every apply logged before
    # 2026-08-01 predates delta_coa, so the key is absent rather than null and
    # .get returns None -- which pd.to_numeric turns into a scalar NaN with no
    # .dropna(). This is the normal state of the historical rows, not an edge
    # case, so it has to degrade to a message rather than raise.
    if "delta_coa" not in applies.columns:
        print("\n  (all applies predate the delta_coa field -- no effect size available)")
        return
    deltas = pd.to_numeric(applies["delta_coa"], errors="coerce").dropna()
    if deltas.empty:
        print("\n  (no apply carries a usable delta_coa -- no effect size available)")
        return
    cheaper = int((deltas < 0).sum())
    print(f"\n  applies with a cost delta  : {len(deltas)}")
    print(f"    moved CHEAPER            : {cheaper}  ({cheaper / len(deltas):.0%})")
    print(f"    median change            : ${deltas.median():,.0f}/year")
    if cheaper:
        print(f"    median saving when cheaper: ${-deltas[deltas < 0].median():,.0f}/year")
    print("\n  Per-year sticker price, before aid, and a switch in the sidebar is\n"
          "  not a switch in enrolment. This measures what the tool changed on\n"
          "  screen, which is the only thing it can observe.")


def main():
    client = load_supabase_client()
    survey_df = fetch_table(client, "survey_responses")
    usage_df = fetch_table(client, "usage_logs")
    pdf_df = fetch_table(client, "pdf_downloads")
    shares_df = fetch_table(client, "scenario_shares")
    scenario_events_df = fetch_table(client, "scenario_events")

    events = build_engagement_frame(pdf_df, shares_df)

    if survey_df.empty:
        # The engagement tables stand on their own -- they carry the same
        # scenario context and (so far) several times more rows, so an empty
        # survey is no reason to skip them.
        print("No survey responses yet -- skipping the perception-change analysis.")
        analyze_engagement(events)
        analyze_switch_rate(scenario_events_df)
        analyze_school_search(usage_df)
        # Still worth running: the pre block writes to usage_logs whether or
        # not anyone reaches the survey, so the funnel exists before the first
        # response does -- and an empty survey with a healthy pre-answer rate
        # is itself the finding.
        _report_presurvey_funnel(usage_df)
        return

    df = survey_df.copy()
    df["perception_change"] = pd.Categorical(df["perception_change"], categories=PERCEPTION_ORDER, ordered=True)

    print_section("OVERVIEW")
    print(f"Total survey responses: {len(df)}")
    if not usage_df.empty:
        pageviews = (usage_df["action"] == "pageview").sum()
        if pageviews:
            print(f"Total pageviews logged: {pageviews}")
            print(f"Survey response rate: {len(df) / pageviews * 100:.1f}% of pageviews")
    print("\nBy respondent role:")
    print(df["respondent_role"].value_counts().to_string())
    print("\nBy expected HS graduation year:")
    print(df["hs_graduation_year"].value_counts().to_string())
    print("\nOverall perception-change breakdown:")
    print(df["perception_change"].value_counts().reindex(PERCEPTION_ORDER).to_string())

    print_section(
        "PERCEPTION CHANGE vs DEBT-TO-INCOME RATIO",
        "Does a higher loan-to-starting-salary ratio correlate with a bigger reported shift?",
    )
    df["dti_bucket"] = df["scenario_a_dti_ratio"].apply(bucket_dti)
    crosstab_pct(df, "dti_bucket")

    print_section(
        "PERCEPTION CHANGE vs 10-YEAR ROI %",
        "Does a stronger (or negative) ROI correlate with a bigger reported shift?",
    )
    df["roi_bucket"] = df["scenario_a_roi_pct"].apply(bucket_roi)
    crosstab_pct(df, "roi_bucket")

    print_section("PERCEPTION CHANGE vs RESPONDENT ROLE")
    crosstab_pct(df, "respondent_role")

    print_section("PERCEPTION CHANGE vs TOP 10 MAJORS EXPLORED")
    top_majors = df["scenario_a_major"].value_counts().head(10).index
    crosstab_pct(df[df["scenario_a_major"].isin(top_majors)], "scenario_a_major")

    print_section(
        "COMPARE MODE USERS vs SINGLE-SCENARIO USERS",
        "Did seeing two scenarios side by side (with an ROI delta) change perception more often?",
    )
    df["used_compare_mode"] = df["scenario_b_major"].notna()
    crosstab_pct(df, "used_compare_mode")
    compare_users = df[df["used_compare_mode"]]
    if not compare_users.empty and compare_users["roi_pct_delta"].notna().any():
        print(f"\nAmong Compare Mode users, average |ROI % delta| between scenarios: "
              f"{compare_users['roi_pct_delta'].mean():.1f} points")

    print_section("MOST EXPLORED SCHOOLS")
    schools = df["scenario_a_school_name"].dropna()
    print(schools.value_counts().head(10).to_string() if not schools.empty else "  (no schools entered yet)")

    print_section("FEEDBACK TEXT: TOP WORDS BY PERCEPTION GROUP")
    feedback = df.dropna(subset=["feedback_text"])
    feedback = feedback[feedback["feedback_text"].str.strip() != ""]
    if feedback.empty:
        print("  (no written feedback yet)")
    else:
        for group in PERCEPTION_ORDER:
            group_df = feedback[feedback["perception_change"] == group]
            if group_df.empty:
                continue
            words = []
            for text in group_df["feedback_text"]:
                words += [w.strip(".,!?\"'()").lower() for w in text.split()]
            words = [w for w in words if w and w not in STOPWORDS]
            top = Counter(words).most_common(8)
            print(f"\n{group} (n={len(group_df)} with written feedback):")
            print("  " + (", ".join(f"{w} ({c})" for w, c in top) if top else "(no notable words)"))

    analyze_engagement(events)
    analyze_switch_rate(scenario_events_df)
    analyze_school_search(usage_df)
    analyze_paired_shift(survey_df, usage_df)
    analyze_instrument_agreement(survey_df)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        OUTPUT_DIR.mkdir(exist_ok=True)

        for bucket_col, order, title, fname in [
            ("dti_bucket", ["<0.5x", "0.5-1x", "1-1.5x", "1.5-2x", "2x+"],
             "Perception Change by Debt-to-Income Ratio", "perception_by_dti.png"),
            ("roi_bucket", ["Negative", "0-50%", "50-100%", "100-200%", "200%+"],
             "Perception Change by 10-Year ROI %", "perception_by_roi.png"),
        ]:
            plot_df = df.dropna(subset=[bucket_col])
            if plot_df.empty:
                continue
            bucketed = pd.Series(
                pd.Categorical(plot_df[bucket_col], categories=order, ordered=True), name=bucket_col
            )
            counts = pd.crosstab(bucketed, plot_df["perception_change"].reset_index(drop=True))
            fig, ax = plt.subplots(figsize=(8, 5))
            counts.plot(kind="bar", stacked=True, ax=ax)
            ax.set_title(title)
            ax.set_xlabel("")
            ax.set_ylabel("Responses")
            ax.legend(title="Perception change", bbox_to_anchor=(1.02, 1), loc="upper left")
            fig.tight_layout()
            fig.savefig(OUTPUT_DIR / fname)
            plt.close(fig)

        print_section("CHARTS SAVED")
        print(f"Saved to {OUTPUT_DIR}/")
    except ImportError:
        print_section("CHARTS SKIPPED")
        print("matplotlib not installed -- run `pip install matplotlib` to also save bar charts.")


if __name__ == "__main__":
    main()

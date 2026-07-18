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
"""

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

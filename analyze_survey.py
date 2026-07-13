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
    response = client.table(table_name).select("*").execute()
    return pd.DataFrame(response.data)


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


def main():
    client = load_supabase_client()
    survey_df = fetch_table(client, "survey_responses")
    usage_df = fetch_table(client, "usage_logs")

    if survey_df.empty:
        print("No survey responses yet -- nothing to analyze.")
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

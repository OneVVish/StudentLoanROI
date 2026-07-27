# Student Loan Payoff & Major ROI Calculator

A Streamlit app that models the real financial outcome of a major/loan
choice: standard vs. income-driven repayment, take-home pay after real
2024 federal/state taxes, cost-of-living by city, and a 10-year ROI
comparison against a debt-free high school graduate. Built as a summer
portfolio project with a companion research paper on how this kind of tool
influences student decision-making — the app also logs anonymous usage
and collects an in-app survey toward that end.

Salary data comes from real BLS OEWS occupation wage percentiles (not
guesses), taxes use real 2024 IRS/state brackets, and cost of living uses
BEA Regional Price Parities. Medicine, Law, and Athletic Training model the
actual multi-year delay (med/law school, residency) before professional
earnings begin, instead of pretending a 4-year degree leads straight into
a doctor's or lawyer's salary. Full sourcing is in the app's "Methodology &
Sources" section at the bottom of the page.

## Setup

Requires Python 3.9+.

```bash
git clone https://github.com/OneVVish/StudentLoanROI.git
cd StudentLoanROI
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

This opens the app at `http://localhost:8501` in your browser.

## Optional: College Scorecard API key

The sidebar's "College Scorecard Lookup" pulls real tuition/debt figures
for the school you enter. It works out of the box with the public
`DEMO_KEY` (rate-limited). For regular use, get a free key at
[api.data.gov/signup](https://api.data.gov/signup/) and paste it into the
sidebar field.

## Data storage (Supabase)

Usage/survey logging is backed by a [Supabase](https://supabase.com) Postgres
database instead of local files, since Streamlit Community Cloud's
filesystem is ephemeral — local CSVs would silently get wiped on every
sleep/restart, which defeats the point of collecting this data for the
companion research paper.

To run the app yourself, you need your own free Supabase project:

1. Sign up at [supabase.com](https://supabase.com) (GitHub login works) and
   create a new project (free tier).
2. In the project's SQL Editor, run the schema below. Five tables:
   `usage_logs` (one row per pageview/event), `survey_responses` (one row
   per feedback submission), `pdf_downloads` (one per PDF report),
   `scenario_shares` (one per "Share Scenario" click), and
   `scenario_events` (one per distinct major/school a visitor tries, so the
   path through the app is recoverable and not just the destination). The
   last four all store the same simulation-context columns — Scenario A's inputs and
   results, Scenario B's when Compare Mode is on at save-time, and a
   column per optional Advanced Analysis module — so a response can be
   analyzed against the exact scenario that produced it.

   Every table also carries a `session_id`: a random per-visit UUID shared
   by every row one browser session writes, so events can be joined across
   tables (e.g. "did this survey response come from someone who had just
   downloaded a PDF?"). It's still anonymous — nothing is derived from the
   visitor, and a refresh starts a new id.

   ```sql
   create table usage_logs (
     timestamp text,
     session_id text,
     action text
   );

   create table survey_responses (
     timestamp text,
     session_id text,

     -- Survey-only respondent fields.
     respondent_role text,
     hs_graduation_year text,
     perception_change text,
     feedback_text text,

     -- Session-global scenario context (see build_scenario_context). Each was
     -- previously only in the share link; logged now so the admin dashboard
     -- can break usage down by them. career_data_source is Career-mode only
     -- (inert default in Major mode); cc_mode_b is NULL outside Compare Mode.
     career_data_source text,   -- National / California
     loan_mode text,            -- Simplified / Detailed
     cc_mode_a text,            -- none / fulltime / parttime
     cc_mode_b text,            -- none / fulltime / parttime (Compare Mode only)

     -- Scenario A (see build_scenario_context in app.py).
     scenario_a_school_name text,
     scenario_a_major text,
     scenario_a_loan_amount numeric,
     scenario_a_interest_rate numeric,
     scenario_a_repayment_strategy text,
     scenario_a_starting_salary numeric,
     scenario_a_dti_ratio numeric,
     scenario_a_monthly_payment numeric,
     scenario_a_payoff_years numeric,
     scenario_a_total_interest numeric,
     scenario_a_earnings_premium numeric,
     scenario_a_roi_pct numeric,
     scenario_a_personal_contribution numeric,
     scenario_a_coa_inflation_rate numeric,
     scenario_a_grants_per_year numeric,
     scenario_a_start_year integer,

     -- Scenario B: only populated when Compare Mode is on at save-time,
     -- NULL otherwise.
     scenario_b_school_name text,
     scenario_b_major text,
     scenario_b_loan_amount numeric,
     scenario_b_interest_rate numeric,
     scenario_b_repayment_strategy text,
     scenario_b_starting_salary numeric,
     scenario_b_dti_ratio numeric,
     scenario_b_monthly_payment numeric,
     scenario_b_payoff_years numeric,
     scenario_b_total_interest numeric,
     scenario_b_earnings_premium numeric,
     scenario_b_roi_pct numeric,
     scenario_b_personal_contribution numeric,
     scenario_b_coa_inflation_rate numeric,
     scenario_b_grants_per_year numeric,
     scenario_b_start_year integer,
     roi_pct_delta numeric,

     -- Optional Advanced Analysis modules (see build_module_context);
     -- each stays NULL unless that module is switched on.
     prestige_mode_active boolean,
     scenario_a_prestige_tier text,
     scenario_b_prestige_tier text,
     ai_mode_active boolean,
     scenario_a_ai_risk_level text,
     scenario_b_ai_risk_level text,
     future_forecasting_active boolean,
     future_plan_selected text,
     scenario_b_future_plan_selected text,
     apprenticeship_active boolean,
     apprenticeship_net_position numeric,
     apprenticeship_earnings_premium numeric,
     apprenticeship_used_profession_data boolean,
     apprenticeship_label text
   );

   -- Same scenario context, minus the four survey-only respondent fields.
   create table pdf_downloads (like survey_responses);
   alter table pdf_downloads
     drop column respondent_role,
     drop column hs_graduation_year,
     drop column perception_change,
     drop column feedback_text;

   create table scenario_shares (like pdf_downloads);

   -- One row per distinct major/school selection a session lands on, so a
   -- visitor who switches major after seeing a bad DTI leaves a trace of the
   -- switch itself, not just of where they ended up. Order by event_seq, not
   -- timestamp: timestamps come from the visitor's own clock and can tie.
   create table scenario_events (like pdf_downloads);
   alter table scenario_events add column event_seq integer;
   create index scenario_events_session_id_idx on scenario_events (session_id, event_seq);
   ```

   The above is the schema for a **fresh** project. If you already created
   these tables from an earlier version of this README, run
   [`migrations.sql`](migrations.sql) instead of recreating them — it adds
   what's since been introduced, without touching existing rows.

   These inserts fail gracefully — a missing table or column is caught and
   logged as a failure rather than raised, so an incomplete schema doesn't
   crash the app, it just silently drops the data. That cuts both ways: a
   forgotten column looks identical to "nobody used that feature," since
   Postgres rejects the entire row and the visit disappears from the data
   rather than landing with blank fields. If you later add a field to
   `build_scenario_context`/`build_module_context`, add the matching column
   to all three tables and record it in `migrations.sql`.
3. From **Project Settings → API**, copy the **Project URL** and the
   **anon public API key**.
4. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and
   fill in those two values:
   ```toml
   [connections.supabase_connection]
   SUPABASE_URL = "https://your-project-ref.supabase.co"
   SUPABASE_KEY = "your-anon-public-key"
   ```
   `secrets.toml` is gitignored — never commit real credentials.

The sidebar's "Admin Analytics View" checkbox reads all four tables back
to show usage metrics and survey results; you can also browse them
directly in Supabase's Table Editor. The checkbox is hidden by default —
press Ctrl+Shift+A, or visit the app with `?admin=1` in the URL, to
reveal it.

## Deploying to Streamlit Community Cloud

1. Push your code to a GitHub repo (this one's already at
   [github.com/OneVVish/StudentLoanROI](https://github.com/OneVVish/StudentLoanROI)).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with
   GitHub, and click **Create app → "Yup, I have an app."**
3. Pick your repo, branch `main`, and file path `app.py`.
4. In **Advanced settings → Secrets**, paste the same
   `[connections.supabase_connection]` block from your local
   `secrets.toml` — this is how the deployed app gets Supabase credentials
   without them ever being in the git repo.
5. Click **Deploy**.

## Disclaimer

This tool produces educational estimates for a student research project,
not financial advice. Figures are national averages/percentiles and won't
reflect any individual's actual salary, cost of living, or loan terms.

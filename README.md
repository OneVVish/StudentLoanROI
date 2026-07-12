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
2. In the project's SQL Editor, run:
   ```sql
   create table usage_logs (
     timestamp text,
     action text
   );

   create table survey_responses (
     timestamp text,
     perception_change text,
     feedback_text text
   );
   ```
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

The sidebar's "Admin Analytics View" checkbox reads both tables back to
show usage metrics and survey results; you can also browse them directly
in Supabase's Table Editor.

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

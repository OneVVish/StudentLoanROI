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

## Data files

The app writes two CSV files in the working directory as you use it:

- `usage_logs.csv` — anonymous pageview/calculation event timestamps
- `survey_responses.csv` — anonymous survey submissions

Both are created automatically on first use and are safe to delete; the
sidebar's "Admin Analytics View" checkbox reads them back to show usage
metrics and survey results.

## Disclaimer

This tool produces educational estimates for a student research project,
not financial advice. Figures are national averages/percentiles and won't
reflect any individual's actual salary, cost of living, or loan terms.

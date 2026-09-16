# When each source updates, and what goes wrong if you miss it

Every figure this app shows comes from a federal release with its own clock.
This file is the consolidated schedule. The per-dataset traps stay where they
are, in each pipeline script's docstring and in CLAUDE.md; what was missing was
one page answering "when does this move, and how would I know".

**THE FAILURE MODE IS ALWAYS THE SAME AND IT IS ALWAYS SILENT.** A stale
release does not raise, does not look wrong and does not fail a guard. It
reports a different year's answer, confidently, to everyone who used the app in
between. `POVERTY_GUIDELINES_2026` and the SAI tables already carry that warning
individually; this is the same warning for everything else.

**VERIFIED means someone read the publisher's own release history on the date
shown. ASSUMED means it is the pattern the committed vintages imply and nobody
has checked it against the publisher.** Do not promote a row from ASSUMED to
VERIFIED without recording where you read it.

## The schedule

| Source | Feeds | Committed vintage | Cadence | Status |
| --- | --- | --- | --- | --- |
| College Scorecard | `college_coa_clean.csv`, `graduate_debt_clean.csv`, `discipline_outcomes_clean.csv` | release June 10, 2026; cost fields report **2024** | **2 to 5 releases a year, irregular** | VERIFIED 2026-09-15 |
| BLS OEWS | `cleaned_careers.csv`, `state_careers_clean.csv`, `metro_careers_clean.csv`, `metro_wage_index.csv` | May 2025 reference period | annual, released the following spring | ASSUMED |
| BLS CPS usual weekly earnings | `HS_GRAD_SALARY` | **2026 Q2, $994/week** | **quarterly** | VERIFIED 2026-09-16 |
| IPEDS IC/HD | `graduate_tuition_clean.csv`, `professional_tuition_clean.csv`, `cc_costs_clean.csv`, `public4_systems_clean.csv` | IC2023_AY / HD2023 | annual collection; the charges file LAGS the directory | ASSUMED |
| CPS ASEC | `hs_age_profile.csv`, `grad_age_profile.csv` | pppub25 (ASEC 2025) | annual, published mid-September | **VERIFIED 2026-09-16, AND ONE RELEASE BEHIND** |
| HHS poverty guidelines | `POVERTY_GUIDELINES_2026` | 2026 | **every January**, effective mid-month | recorded in CLAUDE.md |
| SAI / Federal Need Analysis Methodology | the whole `?tool=sai` worksheet | 2027-28 guide, issued June 12, 2026 | **every spring, for the next award year** | recorded in CLAUDE.md |
| 34 CFR (loan terms, rates, plans) | the repayment simulators | title 34 as issued August 31, 2026 | **July 1 each year** for terms and rates; eCFR issues continuously | recorded in CLAUDE.md |
| NY Fed college labor market | chapter 0 and chapter 15 figures | 2026 Q2 | quarterly | ASSUMED |

## College Scorecard is the one that surprises, so it is written out

Read from `collegescorecard.ed.gov/data/changelog/` on 2026-09-15. Releases per
calendar year: 2018 four, 2019 five, 2020 three, 2021 five, 2022 four, 2023
**two**, 2024 five, 2025 four, 2026 two so far. **It is not annual and the gaps
are not even.** A six-month silence is normal and so is a six-week one.

**A RELEASE IS NOT A COST REFRESH.** The June 10, 2026 entry updated IPEDS-derived
metrics, an earnings-threshold definition, and six Federal Student Aid
administrative flags. The cost fields did not move. So watching the changelog
for "a new release" over-triggers, and watching for a new cost year
under-triggers if you only check once a year.

**THE COST DATA YEAR LAGS THE RELEASE BY ABOUT TWO YEARS**, and the two numbers
are different things. `clean_college_scorecard.py`'s docstring records how that
was established, and it is the check to repeat: match a handful of committed
`in_state_coa` values against the API's `<year>.cost.attendance.academic_year`
for the same UNITID and see which year hits to the dollar. A wider sweep gets
HTTP 429.

## The two high-school-baseline sources, checked 2026-09-16

Both feed the counterfactual every figure in this app is measured against, and
they were the two rows this file had never verified.

**`HS_GRAD_SALARY` IS CURRENT.** BLS series `LEU0252917300` read from
api.bls.gov: 2026 Q2 is **$994/week**, which is the most recent quarter BLS
has published, and $994 x 52 = $51,688 to the dollar. Both warnings already in
app.py's comment also check out against the API: the series really did fall
$977 to $953 across the 2024 to 2025 turn, and **2025 Q4 genuinely does not
exist** in the data, the shutdown quarter.

Two things worth knowing before anyone refreshes it. **`Q05` IS THE ANNUAL
AVERAGE, NOT A FIFTH QUARTER** -- averaging all five periods double counts the
year and is the obvious way to get this wrong. And the constant is a SINGLE
QUARTER rather than an annual average: 2025's annual average is $966/week
($50,232) and the 2026 quarters so far average $985.50 ($51,246), so $51,688
is the highest reading available. That makes the baseline the most demanding
of the three, which understates every degree's premium, which is the direction
this project errs in deliberately.

**CPS ASEC IS ONE RELEASE BEHIND, BY A DAY.** `asecpub26csv.zip` was published
**2026-09-15** (139.1 MB); the committed profiles are built from
`asecpub25csv.zip` of 2025-09-09. So ASEC lands in mid-September and the next
one is due around September 2027.

Refreshing it is NOT a free vintage bump. `hs_age_profile.csv` supplies the
SHAPE of the baseline's age curve and `grad_age_profile.csv` the shape of the
career plateau after year 10, so a new release moves every premium,
break-even and crossover the model produces. It is the same class of change as
the no-degree metro index parked at
`~/.claude-personal/plans/no-degree-metro-baseline-index.md`, and the two
should be done TOGETHER: both move the baseline, and one seam in
`migrations.sql` is better than two a month apart.

Refreshing `HS_GRAD_SALARY` on its own stays safe meanwhile, by design:
`hs_young_wage_disclosure` reads the profile's `ratio_to_25plus` and never its
dollars, so the sentence stays true across a level change.

## How to check, without a subscription to anything

One sweep, quarterly, about ten minutes:

    collegescorecard.ed.gov/data/            # "This data was last updated <date>"
    collegescorecard.ed.gov/data/changelog/  # what actually changed in it
    bls.gov/oes/tables.htm                   # the newest release year
    nces.ed.gov/ipeds/datacenter/data/       # IC and HD for the next year
    aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines
    fsapartners.ed.gov                       # the next award year's SAI guide

**The two with hard dates do not need a sweep and should be diarised: January
for the poverty guidelines, and the spring SAI guide.** Both have a guard that
must be edited in the same commit, which is deliberate: it forces the new
figures to be read off the source twice. `check_plan_switching.py` holds the
poverty literals, `check_sai_worksheet.py` holds the SAI tables.

## What must move together

- **All three OEWS geographies in one pass.** A metro file one release behind
  does not read as stale data, it reads as a pay cut. CLAUDE.md records the
  M2024/M2025 mismatch that put 18.5% of New York occupations below their own
  national figure.
- **IPEDS IC and HD from the SAME year**, both files, one parse.
- **The book and the site.** `marketing/book/front-copyright.md` prints four
  vintages to the reader: OEWS May 2025, Scorecard June 2026 reporting 2024,
  the 2027-28 SAI guide of June 12 2026, and title 34 as issued August 31 2026.
  A refresh that does not move that page leaves the printed book asserting a
  vintage the calculator no longer uses. The paperback cannot be corrected after
  printing, which is why the page also tells the reader the site will have moved
  on.
- **`migrations.sql` on anything that changes what a stored column MEANS.** A
  vintage bump usually does not; a methodology change usually does.

## What this file is not

It is not a promise that anything is current. It is the list of clocks. The
only authority on whether a committed dataset is stale is the publisher's own
release page, read on the day.

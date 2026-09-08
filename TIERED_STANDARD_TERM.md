# The 2026 Tiered Standard term: set by balance, at entry to repayment, per loan

Recorded 2026-09-07, from 34 CFR 685.208 read off the eCFR versioner API
(title 34, issue date 2026-08-31), after the Parent PLUS guide shipped a
section listing the 10-year Standard and 25-year Extended plans as choices for
a NEW Parent PLUS loan. They are not. This file is the rule, the arithmetic,
what the app assumes, and the gap.

## The rule, verbatim

34 CFR 685.208(c), "Fixed Repayment Plans for Direct Loans Made On or After
July 1, 2026":

> (1) Tiered Standard repayment plan for Direct Loan borrowers who received a
> Direct Loan on or after July 1, 2026.
> (i) Under this repayment plan, a borrower must repay a loan in full by
> making fixed monthly payments over a repayment period that varies with the
> total amount of the borrower's Direct Loans ...
> (iii) Under this repayment plan, if the total amount of Direct Loans at the
> time the borrower is entering repayment, is (A) Less than $25,000, the
> borrower must repay the Direct Loan within 10 years of entering repayment;
> (B) Equal to or greater than $25,000 but less than $50,000 ... within 15
> years; (C) Equal to or greater than $50,000 but less than $100,000 ...
> within 20 years; and (D) Equal to or greater than $100,000 ... within 25
> years. [91 FR 23886, May 1, 2026]

Every plan in 685.208(b), the 10-year Standard, the Extended and the Graduated
plans, is limited to borrowers "who have not received a Direct Loan on or
after July 1, 2026". A borrower with one new loan loses them for that loan.

## What it means

- **A new Parent PLUS loan has ONE fixed plan** and no income-driven one: RAP
  is for the student's own Direct loans, and Parent PLUS is not eligible. There
  is no forgiveness at the end. The parent does not choose the term; the
  balance does.
- **The balance is measured at entry to repayment, per loan.** Each year's PLUS
  loan is its own loan and enters repayment on its own (60 days after
  disbursement unless the parent asks for an in-school deferment). Its term is
  frozen then, against the parent's TOTAL Direct Loans outstanding at that
  moment, and does not lengthen later.
- **So deferring and repaying-as-you-go get different terms.** Defer until the
  student leaves school and all four loans enter together on the whole
  balance, so every one gets the term the total earns (20 years at the $65,000
  cap). Repay each loan as disbursed and the freshman loan is measured against
  a small balance (10 years), each later loan against everything still owed
  (15, 15, then 20 at the cap).
- **The term is a ceiling, not a floor.** Prepayment is allowed and shortens
  it. The default is the slow schedule.
- **The thresholds are the one plannable thing**: total PLUS borrowing under
  $50,000 is a 15-year loan, under $25,000 a 10-year one.

## The arithmetic, from the app's own functions

`calculate_tiered_standard_term` (the bands above) and
`calculate_standard_repayment(balance, 8.5, term_years=term)`, at the 8.5
percent `DEFAULT_GAP_RATE` the calculator assumes for Parent PLUS:

| Balance | Term | Monthly | Total interest |
| --- | --- | --- | --- |
| $20,000 | 10 years | ~$250 | ~$9,800 |
| $40,000 | 15 years | ~$390 | ~$30,900 |
| $65,000 (the cap) | 20 years | ~$560 | ~$70,400 |

For contrast, the same $65,000 on the plans an OLDER loan keeps: 10-year
Standard ~$810 a month and ~$31,700 of interest; 25-year Extended ~$520 and
~$92,000.

## What the tools model (since 2026-09-08)

- **The repayment tool takes two months per federal loan**: `disbursed`
  (months until the money arrives, 0 = already owed) and `entry` (months
  until the first payment), plus a `subsidized` tick. `tiered_terms_at_entry`
  (section 2, beside `calculate_tiered_standard_term`) sets each loan's term
  from everything owed on the day it enters: earlier loans at the balance left
  on their own schedule, disbursed-but-deferred loans at principal plus
  accrual, undisbursed loans at nothing. `entry_deferment` grows and shifts
  each loan through `in_school_deferment` / `apply_in_school_deferment`, and
  the fixed rows chain the results. A loan with every field at 0 is exactly
  the old behaviour, one term from the total.
- **Deferment accrual is SIMPLE interest, capitalised once at entry**, per
  685.202(b)(2). `in_school_deferment` compounded monthly until 2026-09-08 and
  overstated a 4.5-year deferment by about 5 percent; the calculator's
  professional-path deferments moved down by that much with the fix.
- **The income-driven rows still treat every loan as owed and in repayment
  today.** A loan joining a pooled income-driven balance later is a different
  simulator, and the case this exists for (Parent PLUS) has no income-driven
  rows. `ENTRY_MONTHS_NOTE` says so on screen and in the PDF.
- **The guide's three roads are prefilled links** (`rb`, `rr`, `rdis`, `re`,
  `rf=0`) and `check_plan_switching.check_entry_terms` holds them as
  fixtures: 20/20/20/20 deferred, 10/15/15/20 as you go, with a negative
  control that reads today's total for every loan.

## Where it is written for readers

`content/posts/parent-plus-senior-year.md`, section "Repaying it is the
parent's job, on one fixed plan" (#286, #287), and the whole of
`content/posts/parent-plus-slow-road-fast-road.md`: the deferred road
(~$104,900 of interest, last payment 24 and a half years after the freshman
fall), the 10-year-pace road (~$31,700, 13 years) and the as-you-go road
(~$52,300, 23 years, entering two months after each disbursement), with the age at the last payment against a retirement
age of 65. The deferral accrual there is simple interest from each fall's
disbursement, capitalized once when the deferment ends: the parent deferment
in 685.204(b)(2) runs through enrollment AND the six-month post-enrollment
period, so 4.5, 3.5, 2.5 and 1.5 years on the four loans (~$16,600, entering
repayment at ~$81,600). Three more rules the guide states from the eCFR: the
term counts ALL the parent's Direct Loans (own loans and other children's
PLUS included); a prepayment advances the due date unless directed to
principal in writing (685.211(a)(3)); and the loan is discharged on the death
of the parent or the student, or the parent's total disability (685.212(a)). The repayment guide's opening
("the list of repayment plans is two items long") is the same fact from the
student's side.

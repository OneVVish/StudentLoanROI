-- Schema changes to apply to an existing Supabase project, in order.
--
-- README.md's "Data storage (Supabase)" section has the full CREATE TABLE
-- schema for a *fresh* project and already includes everything below --
-- this file is only for projects created before these columns existed.
--
-- Apply by pasting into the Supabase SQL Editor (Project -> SQL Editor).
-- The app's anon API key can't run DDL, so this can't be automated from
-- app.py or analyze_survey.py. Every statement is additive, re-runnable,
-- and touches no existing rows.
--
-- Why this file exists: app.py's insert helpers catch every exception and
-- return False, so a column that exists in build_scenario_context/
-- build_module_context but not in the table doesn't crash anything -- the
-- whole row is silently rejected (PostgREST PGRST204) and that visit
-- vanishes from the data. A forgotten migration here looks exactly like
-- "nobody used that feature", which is worse than a crash for the research
-- the data feeds. Add to this file whenever you add a context field.


-- 2026-07-15: Trade Apprenticeship module columns.
--
-- build_module_context (app.py section 5) returns these whenever the Trade
-- Apprenticeship module is switched on, but only apprenticeship_label was
-- ever added to the tables. Result: every save from a session with that
-- module enabled was rejected outright -- PDF downloads and scenario shares
-- lost silently, survey submissions failing with a visible error. Those
-- sessions are absent from the data rather than blank, so any analysis
-- predating this migration is a sample filtered to "apprenticeship off".
alter table survey_responses
  add column if not exists apprenticeship_active boolean,
  add column if not exists apprenticeship_net_position numeric,
  add column if not exists apprenticeship_earnings_premium numeric,
  add column if not exists apprenticeship_used_profession_data boolean;

alter table pdf_downloads
  add column if not exists apprenticeship_active boolean,
  add column if not exists apprenticeship_net_position numeric,
  add column if not exists apprenticeship_earnings_premium numeric,
  add column if not exists apprenticeship_used_profession_data boolean;

alter table scenario_shares
  add column if not exists apprenticeship_active boolean,
  add column if not exists apprenticeship_net_position numeric,
  add column if not exists apprenticeship_earnings_premium numeric,
  add column if not exists apprenticeship_used_profession_data boolean;


-- 2026-07-15: session_id on all four tables.
--
-- A random per-visit UUID (get_session_id in app.py section 2b), shared by
-- every row a single browser session writes. Without it the four tables
-- can't be joined: you can count PDF downloads and count survey responses,
-- but not tell that a response came from someone who had just downloaded
-- one -- which is the behavioral question the companion research paper
-- actually asks. Still anonymous: nothing is derived from the visitor (no
-- IP, no fingerprint, no cookie), and a refresh starts a fresh id, so it
-- can't identify a person or link separate visits.
--
-- Nullable with no backfill: rows written before this migration have no
-- session to attribute, and NULL says that honestly. Filter them out of any
-- join rather than treating them as one shared session -- a `join ... using
-- (session_id)` would otherwise match every pre-migration row to every
-- other, which is why this is left NULL rather than defaulted to ''.
alter table usage_logs       add column if not exists session_id text;
alter table survey_responses add column if not exists session_id text;
alter table pdf_downloads    add column if not exists session_id text;
alter table scenario_shares  add column if not exists session_id text;

create index if not exists usage_logs_session_id_idx       on usage_logs (session_id);
create index if not exists survey_responses_session_id_idx on survey_responses (session_id);
create index if not exists pdf_downloads_session_id_idx    on pdf_downloads (session_id);
create index if not exists scenario_shares_session_id_idx  on scenario_shares (session_id);


-- 2026-07-15: scenario_events -- the exploration path, not just the destination.
--
-- Every other table records a scenario only at a commit point (survey
-- submit, PDF download, share), so a visitor who arrives set on pre-med,
-- sees a 2.3x DTI, switches to nursing and downloads a report leaves one
-- row saying "nursing". The switch -- the actual behavioral finding -- is
-- invisible. maybe_log_scenario_event (app.py section 2b) writes one row per
-- distinct major/school selection a session lands on; joined on session_id
-- and ordered by event_seq those rows reconstruct what was tried, in order,
-- which is what makes a per-major switch rate computable.
--
-- Same scenario-context columns as pdf_downloads, so `like` keeps the three
-- in sync automatically -- session_id and the apprenticeship columns above
-- are already part of that shape by the time this runs. Order matters: this
-- statement must come after the migrations above, which is why this file is
-- append-only.
create table if not exists scenario_events (like pdf_downloads);

-- LIKE copies pdf_downloads' id column definition and its NOT NULL, but not
-- the identity/default that generates the value -- an inherited id would
-- therefore reject every insert that doesn't supply one. Drop it rather than
-- inherit a half-copied column. Nothing needs a synthetic key here:
-- survey_responses has no id either, and this table is only ever addressed
-- by (session_id, event_seq).
alter table scenario_events drop column if exists id;

-- Orders events within a session explicitly. Timestamps come from the
-- visitor's own clock (now_local) and can tie or run backwards across the
-- timezone round-trip, so never ORDER BY timestamp within a session -- use
-- event_seq.
alter table scenario_events add column if not exists event_seq integer;

create index if not exists scenario_events_session_id_idx on scenario_events (session_id, event_seq);

-- LIKE copies columns, not privileges. Supabase's default privileges
-- normally grant the anon role access to tables created here, but the app
-- authenticates with the anon key and a table it can't insert into fails
-- exactly like a missing column does -- silently. Explicit and idempotent,
-- matching the access the other four tables already have.
grant select, insert on scenario_events to anon;


-- 2026-07-15: major_explicitly_selected.
--
-- The sidebar lands pre-filled with a concrete profile (Software Developers
-- at UC Berkeley) so there are real numbers on screen before a visitor
-- touches anything. The cost: a student whose intended profession genuinely
-- IS the default never opens the dropdown, and their session is
-- indistinguishable from one where the visitor ignored the calculator --
-- both leave a row reading "Software Developers". This flag separates an
-- answer from an absence.
--
-- FALSE means "we don't know", not "the visitor disagreed with the default".
-- Rows with major_explicitly_selected = false must be excluded from any
-- analysis treating the major as a choice (the paper's H1 DTI stratification
-- and Table 4 switch rate both qualify) rather than counted as Software
-- Developers, which would manufacture a finding out of the app's own
-- default. Arriving via a share link with ?major= set also leaves this
-- false: that major came from whoever built the link.
--
-- Not added to usage_logs, which records only pageviews and carries no
-- scenario at all.
alter table survey_responses add column if not exists major_explicitly_selected boolean;
alter table pdf_downloads    add column if not exists major_explicitly_selected boolean;
alter table scenario_shares  add column if not exists major_explicitly_selected boolean;
alter table scenario_events  add column if not exists major_explicitly_selected boolean;


-- 2026-07-15: roi_horizon_years.
--
-- The ROI window used to be a fixed 10 years, and that constant was quietly
-- deciding outcomes rather than measuring them: Medicine spends 4 years in
-- school and 3 in residency, so a 10-year view counts 3 years of attending
-- salary against 7 of training while repaying med school inside the same
-- window, and reports a doctor as ~$146k behind a high school graduate. At
-- 15 years the same model says +$469k. It's now a visitor-selected sidebar
-- control (10/15/20/30), so the horizon has to be recorded alongside every
-- result it produced.
--
-- Without this column, scenario_a_roi_pct and scenario_a_earnings_premium
-- are not comparable across rows -- a 30-year ROI and a 10-year ROI are
-- different quantities sharing a column name, and pooling them silently
-- averages incommensurable numbers. Any analysis that strata-fies on ROI
-- (the paper's H1 does) must group by or filter on this.
--
-- Rows predating this are NULL and were all computed at 10 years; treat NULL
-- as 10 rather than dropping them, unlike major_explicitly_selected where
-- NULL genuinely means unknown.
alter table survey_responses add column if not exists roi_horizon_years integer;
alter table pdf_downloads    add column if not exists roi_horizon_years integer;
alter table scenario_shares  add column if not exists roi_horizon_years integer;
alter table scenario_events  add column if not exists roi_horizon_years integer;


-- 2026-07-15: experiment_arm -- randomised contrast-framing condition.
--
-- 'contrast' = the dual-scenario view was open at page load (Software
-- Developers vs Humanities); 'single' = one scenario. Assigned by hashing
-- session_id (get_experiment_arm, app.py section 2b), so it is a fair coin
-- that is stable across reruns.
--
-- Why it exists: while the dual-scenario view was purely opt-in, the paper's
-- secondary hypothesis -- does contrast framing move perception beyond DTI
-- disclosure alone? -- could not be tested at any sample size. Visitors who
-- enabled the comparison were self-selected on engagement and prior
-- uncertainty, so exposure to the manipulation was an outcome of the
-- respondent's disposition. That confound lived in the assignment
-- mechanism, not in the noise, so no amount of data would have separated
-- "framing works" from "the sort of person who compares also reports
-- changing their mind". Randomising the initial state fixes it.
--
-- ANALYSE INTENT-TO-TREAT ON THIS COLUMN. Visitors may still toggle the
-- view, so conditioning on whether a comparison was actually used (i.e. on
-- scenario_b_major being present) reintroduces exactly the self-selection
-- the randomisation removes. Compare arms, not behaviours.
--
-- Sessions arriving via a shared ?compare= link are excluded from the
-- randomised analysis: the link's explicit state overrides the assignment,
-- so their initial view and their arm can disagree.
--
-- Rows predating this are NULL and were all effectively 'single' (the view
-- defaulted off), but they are not randomised and must not be pooled with
-- the 'single' arm.
alter table survey_responses add column if not exists experiment_arm text;
alter table pdf_downloads    add column if not exists experiment_arm text;
alter table scenario_shares  add column if not exists experiment_arm text;
alter table scenario_events  add column if not exists experiment_arm text;

create index if not exists survey_responses_experiment_arm_idx on survey_responses (experiment_arm);


-- 2026-07-15: dataset_mode -- which question the visitor was asking.
--
-- 'Major' = NY Fed per-major data ("what if I study Computer Science?"),
-- 'Career' = BLS per-occupation data ("what if I become a Software
-- Developer?"). Selected by the sidebar's "Choose by" control.
--
-- These are not two views of one number, they are different quantities. A
-- Major-mode salary is what everyone who studied that subject earns,
-- underemployed graduates included. A Career-mode salary is what people
-- already doing that job earn, and assumes the visitor becomes one. At the
-- landing defaults that's a $103,034 vs $629,578 ten-year premium for
-- Computer Science vs Software Developers -- a $526k gap in a column called
-- scenario_a_earnings_premium.
--
-- So: GROUP BY OR FILTER ON THIS in any analysis touching salary, ROI,
-- earnings premium or break-even, exactly as with roi_horizon_years.
-- Pooling the two modes averages incommensurable numbers.
--
-- Rows predating this are NULL and were all Career-mode (the only dataset
-- that existed).
alter table survey_responses add column if not exists dataset_mode text;
alter table pdf_downloads    add column if not exists dataset_mode text;
alter table scenario_shares  add column if not exists dataset_mode text;
alter table scenario_events  add column if not exists dataset_mode text;


-- 2026-07-15: traffic_source -- which outreach a visit came from.
--
-- Read from a ?src= tag on the URL (e.g. /?src=jefferson_econ), set by
-- whoever builds the link. NULL for organic traffic, which is the normal
-- case -- deliberately NULL rather than a fabricated 'direct'.
--
-- Recruitment is the binding constraint on this research, and without this
-- every visit is not merely anonymous but sourceless: forty arrivals the
-- week a counsellor forwards the link are indistinguishable from a class
-- visit, a newsletter, or the author's own testing. This makes "which
-- outreach worked" answerable and lets self-testing be excluded.
--
-- Still anonymous: it identifies a CHANNEL chosen by the link's author, not
-- a person, and carries nothing about the visitor. usage_logs gets it too --
-- that's the only table recording visits that never reach a commit point,
-- which is exactly where attribution matters most (a channel that produces
-- pageviews but no engagement is a finding).
alter table usage_logs       add column if not exists traffic_source text;
alter table survey_responses add column if not exists traffic_source text;
alter table pdf_downloads    add column if not exists traffic_source text;
alter table scenario_shares  add column if not exists traffic_source text;
alter table scenario_events  add column if not exists traffic_source text;

create index if not exists usage_logs_traffic_source_idx on usage_logs (traffic_source);


-- NOTE: the horizon-change and compare-toggle events added at the same time
-- need no NEW COLUMN of their own -- they go to usage_logs.action, an
-- existing free-text event stream that has only ever carried 'pageview'.
-- They do, however, depend on the usage_logs.traffic_source column above,
-- because they share log_usage_event's single insert: until that column
-- exists, every usage_logs write is rejected whole (PGRST204), pageviews
-- included. Run this file before deploying.
--     horizon_changed:30
--     compare_toggled:off:arm=contrast
-- Query them with `where action like 'horizon_changed:%'`. Reporting the
-- compare-toggle compliance rate is standard practice for a randomised
-- trial; H2 stays intent-to-treat on experiment_arm regardless.


-- 2026-07-27: admin-dashboard breakdown fields
--   career_data_source, loan_mode, cc_mode_a, cc_mode_b.
--
-- These four choices were previously carried ONLY in share-link params
-- (build_share_params) and the PDF -- never persisted -- so the admin
-- dashboard could not break usage down by any of them. build_scenario_context
-- now emits them, so the four scenario tables need the columns. Until this is
-- applied, every survey/pdf/share/scenario_event write is rejected whole
-- (PGRST204) exactly like a missing column always is here -- run this file
-- BEFORE deploying the app change. usage_logs is untouched (it carries no
-- scenario context), so pageview logging is unaffected either way.
--
--   career_data_source -- 'National' / 'California' wage dataset (Career mode
--     only; the radio is disabled in Major mode, so read it together with
--     dataset_mode -- a Major-mode row's value is just the inert default).
--   loan_mode          -- 'Simplified' (school median debt) / 'Detailed'
--     (cost/aid inputs). Global, one per session.
--   cc_mode_a/b        -- community-college path: 'none' / 'fulltime' /
--     'parttime'. cc_mode_b is only set in Compare Mode (NULL otherwise).
--
-- Rows predating this are NULL: unknown, not a default -- exclude from the
-- relevant breakdown rather than counting as 'National'/'Simplified'/'none'.
alter table survey_responses
  add column if not exists career_data_source text,
  add column if not exists loan_mode text,
  add column if not exists cc_mode_a text,
  add column if not exists cc_mode_b text;

alter table pdf_downloads
  add column if not exists career_data_source text,
  add column if not exists loan_mode text,
  add column if not exists cc_mode_a text,
  add column if not exists cc_mode_b text;

alter table scenario_shares
  add column if not exists career_data_source text,
  add column if not exists loan_mode text,
  add column if not exists cc_mode_a text,
  add column if not exists cc_mode_b text;

alter table scenario_events
  add column if not exists career_data_source text,
  add column if not exists loan_mode text,
  add column if not exists cc_mode_a text,
  add column if not exists cc_mode_b text;

-- ---------------------------------------------------------------------------
-- 2026-07-30: career_data_source changes MEANING (no DDL required)
--
-- It used to record which wage file the visitor picked from the "Career Salary
-- Data" sidebar radio: exactly 'National' or 'California'. That control is
-- gone. The wage basis now follows the selected city automatically -- the app
-- layers national -> state -> metro and takes the finest geography BLS
-- publishes per occupation -- so this column records the DERIVED state name
-- instead ('New York', 'Texas', ..., or 'National' when the city is
-- "National Average").
--
-- No ALTER TABLE: the column is already text and is retained deliberately
-- rather than dropped, so the pre-change rows stay readable. But it is a
-- BREAK IN THE SERIES, not a widening of it:
--   * Before this date the values are a user CHOICE, and 'California' was
--     reachable alongside any city (that combination is the bug this removed).
--   * After, the value is a FACT about the city and is fully determined by it.
-- Do not pool the two periods in any analysis of geography or of the wage
-- basis. Split on timestamp, or join to city and use that instead.
--
-- Rows written before 2026-07-30 also cannot distinguish "visitor deliberately
-- chose California" from "visitor never touched the control and got the
-- National default", since only the resulting value was stored.

-- ---------------------------------------------------------------------------
-- 2026-07-31: log the two switches that change what an ROI figure MEANS
--
-- hs_baseline_age_aware  -- the high-school baseline follows a real
--   age-earnings curve (true) or is BLS's flat age-25+ median (false).
--   DEFAULTS TRUE from this deploy. Every row before it was computed on the
--   flat baseline and carries NULL here; treat NULL as false.
-- count_foregone_earnings -- whether wages given up while enrolled are charged
--   against the degree. Defaults false, unchanged, but it was never logged
--   either, so the same NULL-means-false rule applies.
--
-- Both were previously unlogged. That was survivable while both defaulted off
-- -- almost every row was the default. It stops being survivable now that one
-- defaults on, because rows would otherwise differ from earlier ones with
-- nothing on the row saying why. Any comparison of earnings_premium or roi_pct
-- across this date MUST condition on these columns.
--
-- Run this BEFORE deploying. Until it exists, PostgREST rejects the whole row
-- on the unknown column (PGRST204) and the event is lost entirely -- it does
-- not merely drop the new field.
alter table survey_responses
  add column if not exists hs_baseline_age_aware boolean,
  add column if not exists count_foregone_earnings boolean;

alter table pdf_downloads
  add column if not exists hs_baseline_age_aware boolean,
  add column if not exists count_foregone_earnings boolean;

alter table scenario_shares
  add column if not exists hs_baseline_age_aware boolean,
  add column if not exists count_foregone_earnings boolean;

alter table scenario_events
  add column if not exists hs_baseline_age_aware boolean,
  add column if not exists count_foregone_earnings boolean;

-- ---------------------------------------------------------------------------
-- 2026-07-31: Trade Apprenticeship module REMOVED -- no DDL, informational
--
-- The module rested on two hardcoded national constants ($52,000 year-1
-- training wage, $86,000 on completion), the second footnoted by
-- apprenticeship.gov itself as Kansas Dept. of Commerce reporting rather than
-- a national census, with an invented growth ramp between them and no
-- per-trade, per-occupation or per-geography variation. It was removed rather
-- than repaired.
--
-- These five columns are DELIBERATELY NOT DROPPED:
--     apprenticeship_active, apprenticeship_net_position,
--     apprenticeship_earnings_premium, apprenticeship_used_profession_data,
--     apprenticeship_label
-- They stop being written from this date and will be NULL on every later row.
-- Dropping them would destroy the history they already hold. A NULL here after
-- 2026-07-31 means "module no longer exists", not "visitor left it off" --
-- those two are indistinguishable in the data, so do not read NULL as opt-out.

-- ---------------------------------------------------------------------------
-- 2026-07-31: program length now drives the loan -- RUN BEFORE DEPLOYING
--
-- scenario_a/b_loan_basis   -- how the loan figure was derived:
--     'cost_based'      Detailed mode, built from Cost of Attendance
--     'reported'        Simplified, school's median completer debt as-is
--     'reported_scaled' Simplified, scaled down for a program shorter than
--                       four years (only at bachelor's-predominant schools;
--                       at a 2-year school the institution median already
--                       describes 2-year completers and is left alone)
--     'no_program'      BLS says the career needs no degree, so zero
-- scenario_a/b_reported_debt   -- the raw College Scorecard figure before any
--     scaling, so a scaled row stays auditable. NULL in Detailed.
-- scenario_a/b_program_years   -- enrollment years charged (0, 2 or 4).
--
-- WHY THIS IS A SERIES BREAK. Until now every career was charged four years of
-- cost and enrollment. From this date, 430 of 825 occupations (52% of the
-- list) that BLS says need no degree are charged NONE, and associate's-degree
-- careers in Simplified mode are charged a scaled figure. The same school and
-- career can therefore produce a different loan_amount, earnings_premium and
-- roi_pct than it did yesterday, for a reason that is not visible in those
-- columns.
--
-- Rows before this date have NULL in all three and CANNOT be reclassified
-- retroactively -- the app didn't record which basis produced them. Do not
-- pool across this date without conditioning on scenario_a_loan_basis; treat
-- NULL as 'cost_based or reported, unknown which'.
--
-- Run this BEFORE deploying. Until the columns exist PostgREST rejects the
-- WHOLE ROW on the unknown column (PGRST204) and the event is lost entirely --
-- it does not merely drop the new fields, and it fails silently.
alter table survey_responses
  add column if not exists scenario_a_loan_basis text,
  add column if not exists scenario_a_reported_debt numeric,
  add column if not exists scenario_a_program_years integer,
  add column if not exists scenario_b_loan_basis text,
  add column if not exists scenario_b_reported_debt numeric,
  add column if not exists scenario_b_program_years integer;

alter table pdf_downloads
  add column if not exists scenario_a_loan_basis text,
  add column if not exists scenario_a_reported_debt numeric,
  add column if not exists scenario_a_program_years integer,
  add column if not exists scenario_b_loan_basis text,
  add column if not exists scenario_b_reported_debt numeric,
  add column if not exists scenario_b_program_years integer;

alter table scenario_shares
  add column if not exists scenario_a_loan_basis text,
  add column if not exists scenario_a_reported_debt numeric,
  add column if not exists scenario_a_program_years integer,
  add column if not exists scenario_b_loan_basis text,
  add column if not exists scenario_b_reported_debt numeric,
  add column if not exists scenario_b_program_years integer;

alter table scenario_events
  add column if not exists scenario_a_loan_basis text,
  add column if not exists scenario_a_reported_debt numeric,
  add column if not exists scenario_a_program_years integer,
  add column if not exists scenario_b_loan_basis text,
  add column if not exists scenario_b_reported_debt numeric,
  add column if not exists scenario_b_program_years integer;


-- ---------------------------------------------------------------------------
-- 2026-07-31 -- DATA QUALITY NOTE, no DDL.
--
-- Local browser verification on 2026-07-30 and 2026-07-31 was run WITHOUT the
-- ?test=1 flag, against production Supabase (secrets.toml has no separate dev
-- project). Those sessions wrote `pageview` rows to usage_logs and rows to
-- scenario_events exactly as a real visit would.
--
-- Counted at the time of writing: 82 usage_logs rows and 76 scenario_events
-- rows carry a timestamp >= 2026-07-30T00:00:00. An unknown but material
-- share of those is self-testing. survey_responses, pdf_downloads and
-- scenario_shares took ZERO new rows in that window -- the survey was never
-- submitted, no PDF was downloaded and no share link was created during
-- testing -- so the primary dependent measure and both high-intent
-- behavioural proxies are UNAFFECTED.
--
-- Self-test rows cannot be separated from organic ones after the fact:
-- traffic_source is NULL for an untagged real visit exactly as it is for an
-- untagged local one. The 13 rows in that window carrying a src tag
-- (LACC, ARC, 3Dcab, ys) are genuine recruitment traffic and are safe.
--
-- Guidance for anyone computing engagement/funnel figures:
--   * Treat untagged usage_logs and scenario_events rows in
--     [2026-07-30, 2026-07-31] as unusable. Do not try to filter by
--     session_id -- local sessions look like any other.
--   * Session COUNTS and pageview-derived funnel rates for those two days are
--     inflated. Conversion rates with a survey/PDF/share numerator are still
--     valid in the numerator but have an inflated denominator.
--   * Nothing before 2026-07-30 is affected.
--
-- Prevention: CLAUDE.md now requires ?test=1 for every local run. Every
-- writer already honours st.session_state.test_mode; the flag was simply not
-- used.


-- ---------------------------------------------------------------------------
-- 2026-07-31: within-session pre/post instrument.
--
-- The app's only outcome measure was perception_change: one retrospective
-- self-report, four options, no neutral category, collected from the
-- self-selected minority who scroll ~1,000 lines to the bottom. It asks a
-- respondent to introspect on a change they may never have noticed, and there
-- was nothing to difference it against -- no question was asked anywhere
-- before the numbers appeared. These columns hold a measurement taken BEFORE
-- the results are read, and the same one taken after.
--
-- SEVEN COLUMNS, ONE TABLE. This is deliberate and is the opposite of the
-- four-table discipline elsewhere in this file. Those blocks add fields that
-- enter build_scenario_context, which is spread into four different inserts.
-- These are survey-only fields, passed as named parameters to
-- save_survey_response exactly like perception_change and respondent_role.
-- pdf_downloads, scenario_shares and scenario_events get NOTHING. Putting
-- them in the context dict instead would require the four-table migration and
-- walk straight into the PGRST204 whole-row-rejection failure this file
-- exists to prevent.
--
-- Values are stable CODES, not display prose. PERCEPTION_ORDER in
-- analyze_survey.py is a hand-copy of app.py's radio labels: reword one side
-- and the cross-tab silently reindexes to NaN. Survivable for a category
-- count; not for an ORDINAL item whose analysis SUBTRACTS two values, where a
-- broken map yields a wrong number instead of an obvious blank.
--
--   pre_schools_considered   s0 | s1 | s2 | s3 | s4 | s5plus | unsure | skip
--   post_schools_considered  Same codes. Outcome = idx(post) - idx(pre).
--                            "unsure" is a RESPONSE, not a missing value, and
--                            must be tabulated then excluded BY NAME -- never
--                            by dropna(), and never averaged into a midpoint.
--
--   pre_borrow_willingness   n0 | b1 | b2 | b3 | b4 | b5 | undecided
--   post_borrow_willingness  | n_a | skip
--                            WILLINGNESS ("the most you would take on"), not
--                            expectation. The sidebar displays a loan figure
--                            at page load, so an "expectation" asked after
--                            exposure is partly a reading test of a number
--                            already on screen; a willingness threshold
--                            appears nowhere in the app and has to be
--                            generated. The column name says so on purpose.
--                            n_a = the question was never put (Counselor),
--                            which is NOT the same fact as skip = asked and
--                            declined. Do not collapse them.
--
--   pre_skipped              true when the visitor dismissed the pre block.
--                            Distinct from the pre columns being NULL, which
--                            means it was never shown at all.
--
--   age_attested             true when a Student confirmed 18+. Research
--                            participation is limited to adults; this is the
--                            eligibility record for that, on the row.
--
--   instrument_version       'v1'. Without it, "declined the pre" (version
--                            set, pre columns NULL) and "predates the pre"
--                            (version NULL) are the SAME NULL, and the
--                            denominator of the pre-response rate is silently
--                            wrong. Same reasoning as hs_baseline_age_aware
--                            above: keep writing a near-constant column
--                            because it is the only thing telling two eras
--                            apart. Bump it whenever an option set or a
--                            question's wording changes.
--
-- Run this BEFORE deploying the code that writes these fields. Until the
-- columns exist PostgREST rejects the WHOLE ROW on the unknown column
-- (PGRST204) and the survey response is lost entirely -- it does not merely
-- drop the new field.
alter table survey_responses
  add column if not exists pre_schools_considered  text,
  add column if not exists pre_borrow_willingness  text,
  add column if not exists post_schools_considered text,
  add column if not exists post_borrow_willingness text,
  add column if not exists pre_skipped             boolean,
  add column if not exists age_attested            boolean,
  add column if not exists instrument_version      text;


-- NOTE, no DDL: the pre AND post answers also ride usage_logs.action and need
-- no columns of their own -- same treatment as horizon_changed: above.
--     presurvey_shown
--     presurvey_answered:role=student:considering=s3:borrow=b3:seq=1:arm=single:v=v1
--     presurvey_skipped
--     presurvey_ineligible_minor
--     postsurvey_answered:considering=s2:borrow=b2:pre=1:v=v1
--     survey_blocked_minor
-- Query with `where action like 'presurvey_%' or action like 'postsurvey_%'`.
--
-- Why both there and here, which looks like duplication and is not: the
-- survey-row copy makes a pair atomic (a pre can never be mispaired with
-- another session's post, and it survives a swallowed usage_logs exception --
-- every writer in app.py catches and returns silently). The usage_logs copy
-- is the ONLY record of a session that answered the pre and never reached the
-- survey, which is most of them and the entire basis for measuring drop-off.
--
-- KNOWN LIMITATION, not fixable without breaking a privacy commitment: a
-- refresh resets survey_submitted, so one person can submit more than once as
-- apparently distinct respondents, and the second visit's "pre" is collected
-- post-exposure. Closing it needs a durable identifier, which the app
-- deliberately does not store (get_session_id: a per-visit uuid4, no cookie,
-- no fingerprint). Report the session-to-response ratio so the inflation is
-- visible rather than silent.

-- ============================================================================
-- 2026-08-01  Diagnostic probe rows in survey_responses -- DELETE THESE
-- ============================================================================
-- While diagnosing the "Something went wrong saving your response" failure,
-- five rows were inserted into survey_responses to establish where the insert
-- broke. They are all tagged traffic_source = 'schema_probe' and are NOT
-- visitor data. The anon key can insert but not delete (RLS), so they must be
-- removed here.
--
-- Run this. It is safe: 'schema_probe' is not a value any real visit can carry,
-- because traffic_source only ever holds a ?src= tag chosen by whoever built
-- the link.

delete from survey_responses where traffic_source = 'schema_probe';

-- ============================================================================
-- 2026-08-01  Delete the 5 pre-approval survey responses
-- ============================================================================
-- These are genuine visitor responses, collected 2026-07-12..14, BEFORE any
-- IRB determination existed. They are human-subjects data gathered without the
-- approval that should have preceded it, so they are being removed rather than
-- retained-and-excluded. Retaining them for analysis while acknowledging they
-- were improperly collected is the thing the deletion is meant to avoid.
--
-- IRREVERSIBLE, and there is no backup. These 5 rows are the ENTIRE survey
-- dataset -- after this the table is empty and every survey figure the paper
-- might have cited is gone. That is the intended outcome, not a side effect.
--
-- The one substantive finding in them does not require keeping them: a
-- self-identified student wrote "I am not really sure what any of this means."
-- That is a usability signal about the app's readability for its actual target
-- reader, and it survives as a note here without retaining the row.
--
-- Predicate rather than a bare `delete from survey_responses` so that a
-- response arriving between now and when this is run is not silently caught up
-- in it. Check the count first:
--
--   select count(*) from survey_responses
--    where traffic_source is null and timestamp < '2026-07-15';   -- expect 5

delete from survey_responses
 where traffic_source is null
   and timestamp < '2026-07-15';

-- After both statements: select count(*) from survey_responses;  -- expect 0

-- ============================================================================
-- 2026-08-01  ?research=1 gate REMOVED -- collection is open
-- ============================================================================
-- No schema change. Recorded here because this is the line every later
-- analysis has to condition on, and nothing in the data marks it.
--
-- The pre/post instrument was held behind ?research=1 from 2026-07-31 until
-- 2026-08-01 because the human-subjects determination had not been obtained.
-- It now has been, and the gate is gone: the pre-question and the exit survey
-- render for every visitor.
--
--   >>> FILL IN: determination reference (protocol number, or the exemption
--   >>> category relied on, and the date and issuing body). It is deliberately
--   >>> not invented here. Copy the same reference into the paper's 5.1b.
--
-- What this means for the data:
--
--   * survey_responses is EMPTY as of this date -- the 5 pre-approval rows
--     were deleted above. Every row from here on is post-determination, so
--     there is no "collected before approval" subset to exclude. That is the
--     whole point of having deleted them rather than kept them.
--   * usage_logs, scenario_events, pdf_downloads and scenario_shares were
--     never gated and span both eras. They are behavioural, not instrument
--     data, and were unaffected by the gate.
--   * Response volume before and after 2026-08-01 is not comparable: the
--     denominator changed from "visitors given a research link" to "all
--     visitors". Any rate computed across this date is meaningless.
--
-- Two protections remain and were never part of this gate. Do not remove them
-- on the strength of a determination that covers something else:
--   * research_participation_allowed() withholds the instrument from a
--     self-identified student who has not attested to RESEARCH_MIN_AGE.
--   * The consent notice renders above the form, before anything is answered.

-- ============================================================================
-- 2026-08-01  Interpretability columns -- RUN THIS BEFORE DEPLOYING THE CODE
-- ============================================================================
-- scenario_events already carries 68 columns, but none of them says WHERE the
-- numbers came from. Every earnings_premium and roi_pct is cost-of-living
-- adjusted to a city and metro-scaled to that city's wages, so a logged
-- $292,603 premium is indistinguishable from a national-average one. The loan
-- figure has the same defect: in-state vs out-of-state Cost of Attendance
-- differs by $34,200/year at Berkeley alone, and which one produced
-- scenario_a_loan_amount is not recorded.
--
-- Four columns, four tables. Run all four blocks BEFORE the code that writes
-- them: PostgREST rejects the WHOLE ROW on an unknown column (PGRST204), so a
-- forgotten table silently drops every event from sessions that reach it.

alter table survey_responses
  add column if not exists city text,
  add column if not exists scenario_a_in_state boolean,
  add column if not exists scenario_b_in_state boolean,
  add column if not exists wage_geography_level text;

alter table pdf_downloads
  add column if not exists city text,
  add column if not exists scenario_a_in_state boolean,
  add column if not exists scenario_b_in_state boolean,
  add column if not exists wage_geography_level text;

alter table scenario_shares
  add column if not exists city text,
  add column if not exists scenario_a_in_state boolean,
  add column if not exists scenario_b_in_state boolean,
  add column if not exists wage_geography_level text;

alter table scenario_events
  add column if not exists city text,
  add column if not exists scenario_a_in_state boolean,
  add column if not exists scenario_b_in_state boolean,
  add column if not exists wage_geography_level text;

-- Reading these later:
--
--   * Rows written before this date have all four NULL. NULL means NOT
--     RECORDED -- never "national", never "in-state". Any analysis that treats
--     a NULL wage_geography_level as a national wage is inventing a value; the
--     national/state/metro fallback has been live since well before this
--     column existed, so those rows genuinely could be any of the three.
--   * wage_geography_level is Scenario A's. In Compare Mode both scenarios
--     share one city, so they share the level unless the two occupations
--     differ in which geographies publish them -- which they can. Treat it as
--     Scenario A's basis, not the row's.
--   * In CAREER mode it is always one of 'national' / 'state' / 'metro'. The
--     app stamps a level only when a state or metro overlay replaces the
--     national spine, so an un-overlaid occupation (39 of 836 in San
--     Francisco) has no stamp -- build_scenario_context resolves that to the
--     literal 'national' rather than writing NULL, because NULL is already
--     what MAJOR mode writes, for the unrelated reason that NY Fed entries
--     carry no OEWS geography at all. So: NULL + dataset_mode='Career' should
--     not occur; if it does, it is a pre-2026-08-01 row, not a national wage.
--
-- NOT added, deliberately: scenario_a/b_breakeven_debt. It is computed inside
-- the single-scenario branch only, after that branch's writer; adding it would
-- produce a column whose missingness correlates PERFECTLY with experiment_arm,
-- which is a worse defect than not having it. It is also redundant --
-- break-even is a deterministic function of major, rate, strategy, horizon and
-- wage index, all of which this row already carries, so it can be recomputed
-- offline exactly as analyze_model.py already does.

-- ============================================================================
-- 2026-08-01  Timestamps before this date are UTC regardless of the visitor
-- ============================================================================
-- No schema change. Recorded because nothing in the data marks the boundary
-- and every timestamp column crosses it.
--
-- now_local() reads the visitor's zone from a ?tz= param set by a JS
-- round-trip. On Streamlit Community Cloud the app runs inside a wrapper
-- iframe, and the script was writing tz to window.top -- the WRAPPER -- while
-- Streamlit reads its query params from the app frame at /~/+/. The parameter
-- therefore never reached Python, and get_user_timezone() fell back to "UTC"
-- for every visitor since the feature shipped. Measured before the fix: 12 of
-- 12 production rows stamped +00:00, and the wrapper's URL carried tz while
-- the app frame's did not.
--
-- Consequence for analysis, on usage_logs, scenario_events, survey_responses,
-- pdf_downloads and scenario_shares alike:
--
--   * Rows before 2026-08-01 ~09:03 UTC are the SERVER's clock. They are not
--     wrong as instants -- the moment is correct -- but the local time of day
--     they imply is not the visitor's. Any "when do people use this" or
--     hour-of-day analysis across this boundary compares server time to local
--     time and will show a spurious shift at this date.
--   * Rows after it carry the visitor's own offset, so a mixed window needs
--     everything normalised to UTC first (they are all tz-aware; just convert)
--     before any hour-of-day bucketing.
--   * The very first pageview of a session can still be UTC even after the
--     fix: there is no tz to read until the JS round-trip completes, which is
--     one rerun later. That is one row per session, not a window.

-- ============================================================================
-- 2026-08-01  Pre-survey now OFF by default; ?research=1 turns it back on
-- ============================================================================
-- No schema change. Recorded because nothing in the data marks it and it
-- changes what a NULL pre_* column means for a whole population of rows.
--
-- The pre-survey renders above the results and is the only real friction in
-- the instrument, so it is now off for ordinary traffic (PRESURVEY_ENABLED =
-- False in app.py). The exit survey stays on for everyone -- it sits at the
-- page bottom where a non-scrolling visitor never meets it, and it carries
-- perception_change, which is the item H1 and H2 are measured on.
--
-- ?research=1 turns BOTH on. Recruitment links already carry ?src=, so
-- &research=1 rides along with them.
--
--   NOTE: ?research=1 meant the OPPOSITE before this date. It was an ethics
--   gate that HID the instrument while no human-subjects determination
--   existed. It now SHOWS it. Same parameter, inverted meaning, on the same
--   day -- do not read a pre-2026-08-01 research=1 row as a recruited visitor.
--
-- For analysis:
--
--   * Paired pre/post data now comes ONLY from ?research=1 arrivals. That is a
--     non-random subset -- recruited, and probably more motivated. It does not
--     threaten H2, which randomises experiment_arm within whoever shows up,
--     but it does bound generalisation.
--   * perception_change will exist for BOTH populations while pre_* exists for
--     only one. Any pooled analysis must condition on which.
--   * pre_* NULL with pre_skipped = false already means "never shown" and now
--     covers two different situations: the pre-feature era, and ordinary
--     traffic after this date. Separate them by timestamp.
--   * instrument_version stays 'v1'. No question text changed; bumping it
--     would falsely signal the instrument itself differs.

-- ============================================================================
-- 2026-08-01  Returning-student mode -- RUN BEFORE DEPLOYING THE CODE
-- ============================================================================
-- Without these, a returning-student session is indistinguishable from a
-- first-time one: the premium and roi_pct are there, but nothing records that
-- they were measured against the visitor's own salary rather than a debt-free
-- high school graduate. Same interpretability gap as city/wage_geography_level,
-- in a new place.
--
-- Six columns, four tables. PostgREST rejects the WHOLE ROW on an unknown
-- column, so a forgotten table silently drops every event from a
-- returning-student session and reads as "nobody used the feature".

alter table survey_responses
  add column if not exists student_mode text,
  add column if not exists current_age integer,
  add column if not exists baseline_salary_now numeric,
  add column if not exists baseline_salary_in_10y numeric,
  add column if not exists existing_debt numeric,
  add column if not exists payoff_age numeric;

alter table pdf_downloads
  add column if not exists student_mode text,
  add column if not exists current_age integer,
  add column if not exists baseline_salary_now numeric,
  add column if not exists baseline_salary_in_10y numeric,
  add column if not exists existing_debt numeric,
  add column if not exists payoff_age numeric;

alter table scenario_shares
  add column if not exists student_mode text,
  add column if not exists current_age integer,
  add column if not exists baseline_salary_now numeric,
  add column if not exists baseline_salary_in_10y numeric,
  add column if not exists existing_debt numeric,
  add column if not exists payoff_age numeric;

alter table scenario_events
  add column if not exists student_mode text,
  add column if not exists current_age integer,
  add column if not exists baseline_salary_now numeric,
  add column if not exists baseline_salary_in_10y numeric,
  add column if not exists existing_debt numeric,
  add column if not exists payoff_age numeric;

-- Reading these later:
--
--   * student_mode is the one that changes what every OTHER column means.
--     'Going back to school' rows have an earnings_premium measured against
--     baseline_salary_now, not against a high school graduate -- the two are
--     not comparable and must never be pooled without conditioning on it.
--   * baseline_salary_* are NULL even in returning mode until the visitor
--     enters both. The app deliberately keeps the old baseline until then, so
--     NULL there means "still measured against a high school graduate", not
--     "missing data".
--   * existing_debt is EXCLUDED from scenario_a_loan_amount and from the ROI
--     by design. It raises payoff_age and the monthly payment only. Adding the
--     two together to get "total debt" is correct; adding it into the ROI
--     denominator is not.
--   * payoff_age is NULL outside returning mode, because current_age is only
--     asked there.


-- ============================================================
-- 2026-08-01 -- repayment_strategy gains a third value (no DDL required)
-- ============================================================
-- No ALTER TABLE: repayment_strategy is already text. What changed is its
-- DOMAIN, and pooling across the change is what will go wrong silently.
--
-- Before today the column held exactly two values:
--     'Standard 10-Year'
--     'Income-Driven Repayment (IDR)'
-- It can now also hold:
--     'Repayment Assistance Plan (RAP)'
--
-- Why: IBR -- which is what the app's IDR model is shaped like -- is closed to
-- loans originated on or after 2026-07-01. From then the income-driven plan is
-- RAP. The app now picks the income-driven option from the scenario's start
-- year, so a start year >= 2026 offers RAP and an earlier one offers IDR.
-- (Source: TICAS, "Comparing Income-Driven Repayment Plans", 2025-09-16.)
--
-- Reading these later:
--
--   * 'Income-Driven Repayment (IDR)' rows written BEFORE 2026-08-01 include
--     sessions whose start_year was 2026 or later -- i.e. borrowers who were
--     shown a plan they could not actually have chosen. After this date, an
--     IDR row implies start_year < 2026. The two eras are not the same
--     population and must not be pooled as "chose income-driven" without
--     conditioning on scenario_a_start_year.
--
--   * Any comparison of payoff_years, total_interest or forgiven amounts
--     across 2026-08-01 is also affected by the two model changes shipped just
--     before this one, both of which move income-driven figures a long way:
--       - non-federal debt (Parent PLUS + private) is no longer forgiven, and
--       - the Parent PLUS and professional-degree caps now bound how much of a
--         loan is federal at all.
--     Treat income-driven rows from before 2026-08-01 as a separate model.
--
--   * RAP and IDR forgive on DIFFERENT terms -- 30 years vs 20 -- so a
--     forgiven amount is only interpretable alongside the strategy that
--     produced it. RAP's interest waiver also means many scenarios that
--     forgave a large balance under IDR now forgive nothing at all.


-- ============================================================
-- 2026-08-01 (later the same day) -- repayment_strategy's DEFAULT changes
-- ============================================================
-- Still no DDL. This is the second domain change to repayment_strategy today
-- and the more disruptive of the two for analysis, because it moves the
-- DEFAULT rather than just adding a value.
--
-- The dropdown for a 2026+ start year -- i.e. every scenario the app can now
-- build -- is:
--     'Repayment Assistance Plan (RAP)'      <- DEFAULT (was Standard 10-Year)
--     '2026 Tiered Standard Plan'            <- new value
-- with these two added only when the visitor ticks "Compare against pre-2026
-- repayment plans" under Advanced Analysis:
--     'Standard 10-Year'
--     'Income-Driven Repayment (IDR)'
--
-- Reading these later:
--
--   * The strategy distribution is NOT comparable across this change. Before
--     it, 'Standard 10-Year' was what a visitor got by not touching the
--     control; after it, that value means someone deliberately enabled the
--     legacy plans AND then chose one. A drop in Standard-10-Year rows is the
--     default moving, not behaviour changing.
--
--   * Symmetrically, RAP rows after this change are mostly untouched defaults,
--     where RAP rows from earlier today were deliberate selections. If the
--     paper treats "chose an income-driven plan" as a behavioural signal, that
--     signal exists only in rows written before this change.
--
--   * enable_legacy_plans is not currently logged. If the distinction above
--     matters, it needs a column on all four tables before it can be
--     recovered -- it cannot be reconstructed after the fact.


-- ============================================================
-- 2026-08-02 -- per-school professional-school debt
-- ============================================================
-- RUN THIS BEFORE DEPLOYING THE CODE THAT WRITES IT. PostgREST rejects the
-- ENTIRE row on an unknown column (PGRST204), so a missing column here does
-- not drop one field -- it drops every survey response, PDF download and share
-- from any session that reached a medical, dental or law scenario, and those
-- visits then look identical to "nobody used the feature".

alter table survey_responses
  add column if not exists prof_school_a text,
  add column if not exists prof_school_b text,
  add column if not exists professional_debt_a numeric,
  add column if not exists professional_debt_b numeric;

alter table pdf_downloads
  add column if not exists prof_school_a text,
  add column if not exists prof_school_b text,
  add column if not exists professional_debt_a numeric,
  add column if not exists professional_debt_b numeric;

alter table scenario_shares
  add column if not exists prof_school_a text,
  add column if not exists prof_school_b text,
  add column if not exists professional_debt_a numeric,
  add column if not exists professional_debt_b numeric;

-- scenario_events too. It is easy to miss because it builds its OWN narrow
-- dict (timestamp/session_id/traffic_source/experiment_arm/event_seq) -- but
-- its call sites pass {**build_scenario_context(...)} and it spreads that, so
-- it receives every scenario column the other three do. Missing it here would
-- drop the per-rerun path data, which is the one table that records a visitor
-- SWITCHING school rather than where they landed.
alter table scenario_events
  add column if not exists prof_school_a text,
  add column if not exists prof_school_b text,
  add column if not exists professional_debt_a numeric,
  add column if not exists professional_debt_b numeric;

-- Reading these later:
--
--   * professional_debt_a's MEANING CHANGED on 2026-08-02. Before it, medical,
--     dental and law school debt was a single national constant per path
--     ($205,000 / $293,900 / $130,000) fully determined by scenario_a_major.
--     After it, a visitor can name their school and the figure becomes that
--     school's Scorecard median -- which ranges from about $48,000 to $330,000
--     for medicine. Any comparison of effective_principal, roi_pct or the
--     federal/private split across that date must condition on prof_school_a.
--
--   * prof_school_a IS NULL means one of two different things, and they are
--     distinguishable only via scenario_a_major: either the path attends no
--     professional school at all (most careers), or it does and the visitor
--     left the picker on the national average. professional_debt_a is NULL in
--     the first case and non-NULL in the second.
--
--   * Both columns are NULL for every row written before this date, including
--     rows whose scenario DID carry professional debt. Treat NULL there as
--     "national constant", not as "no professional school".
--
--   * The school name is stored as free text rather than a UNITID because it is
--     what the picker shows and what a share link carries. It is NOT stable
--     across Scorecard releases -- an institution can be renamed or merged --
--     so join on it with care, and prefer professional_debt_a for arithmetic.


-- ============================================================
-- 2026-08-02 (later) -- graduate degree support
-- ============================================================
-- RUN BEFORE DEPLOYING. Unknown column => PostgREST rejects the ENTIRE row
-- (PGRST204), on all four tables that spread build_scenario_context.

alter table survey_responses
  add column if not exists credential_a text,
  add column if not exists credential_b text,
  add column if not exists graduate_years_a integer,
  add column if not exists graduate_years_b integer,
  add column if not exists grad_school_a text,
  add column if not exists graduate_debt_a numeric;

alter table pdf_downloads
  add column if not exists credential_a text,
  add column if not exists credential_b text,
  add column if not exists graduate_years_a integer,
  add column if not exists graduate_years_b integer,
  add column if not exists grad_school_a text,
  add column if not exists graduate_debt_a numeric;

alter table scenario_shares
  add column if not exists credential_a text,
  add column if not exists credential_b text,
  add column if not exists graduate_years_a integer,
  add column if not exists graduate_years_b integer,
  add column if not exists grad_school_a text,
  add column if not exists graduate_debt_a numeric;

alter table scenario_events
  add column if not exists credential_a text,
  add column if not exists credential_b text,
  add column if not exists graduate_years_a integer,
  add column if not exists graduate_years_b integer,
  add column if not exists grad_school_a text,
  add column if not exists graduate_debt_a numeric;

-- Reading these later:
--
--   * scenario_a_program_years CHANGED MEANING on 2026-08-02 and the change is
--     invisible without credential_a. Before this date every occupation above
--     an associate's resolved to 4. After it, the 113 of 825 occupations BLS
--     enters with a master's or doctorate resolve to 6 or 9 -- so a 4 written
--     before this date and a 4 written after mean different things for those
--     careers, and 6 never appeared at all before it. Condition on
--     credential_a, or on the date, before pooling program years, loan
--     amounts, roi_pct or earnings_premium for any graduate-level occupation.
--
--   * credential_a IS NULL means Major mode with the radio left on Bachelor's,
--     OR a Career-mode occupation whose BLS level is bachelor's-or-below.
--     graduate_years_a distinguishes them only when it is non-NULL; a NULL in
--     both means "no graduate study", which is the common case.
--
--   * graduate_years_a is ADDITIONAL years, not total. A master's is 2 here
--     and 6 in scenario_a_program_years. Subtracting gives the undergraduate
--     portion, which is what the undergraduate Direct limits were applied to.
--
--   * graduate_debt_a IS NULL far more often than not -- only about 20% of
--     school x field cells publish a master's median and 6% a doctoral one, so
--     NULL means "the visitor entered their own cost", not "no graduate debt".
--     grad_school_a is NULL in exactly the same rows.
--
--   * credential_b in Major mode is COPIED from A: there is one credential
--     radio, on the reasoning that a visitor comparing two majors is one
--     person choosing one level. It is independently derived only in Career
--     mode. Do not read a B/A difference in Major mode as a visitor choice.


-- =====================================================================
-- 2026-08-02  usage_logs.action gains "pageview_repayment"
-- =====================================================================
-- NO DDL REQUIRED. usage_logs.action is free text and already carries
-- several shapes ("interaction:<field>", "horizon_changed:<n>",
-- "school_search_run:..."). Nothing to paste into the SQL editor. This
-- section exists because the DATA changed meaning even though the SCHEMA
-- did not -- which is the harder kind to notice later.
--
-- What changed: the standalone repayment page (?tool=repayment) used to
-- log a plain "pageview", identical to a calculator visit. From this date
-- it logs "pageview_repayment" instead.
--
-- Reading this later:
--
--   * "pageview" NARROWED on 2026-08-02. Before this date it meant "any
--     landing, either page". After it, "calculator landing only". A
--     query filtering on action = 'pageview' therefore counts repayment
--     visits before the date and drops them after -- so an unconditioned
--     time series shows calculator traffic FALLING at exactly the point
--     the split shipped, and the drop is entirely definitional. Any
--     comparison spanning this date must either use both actions or
--     restrict to one side of it.
--
--   * Total traffic = both actions. Use:
--         action in ('pageview', 'pageview_repayment')
--     app.py exposes this as PAGEVIEW_ACTIONS; the admin panel's
--     Pageviews metric and the traffic-by-source table both use it.
--
--   * The SURVEY RATE deliberately does NOT. The survey is rendered by
--     the calculator's section 5e and the repayment page never shows it,
--     so its denominator is "pageview" alone. Folding repayment visits
--     in would divide by people who were never asked and report a
--     falling response rate as though they had declined. See
--     analyze_survey.py, which prints the repayment count separately for
--     this reason.
--
--   * There is no way to recover the split retroactively. Repayment
--     visits before this date are indistinguishable from calculator
--     visits in usage_logs -- no column, in that table, records which
--     page was rendered. If a pre-split repayment count is needed, the
--     only proxy is a session_id join against the existing-loan
--     interaction rows, which undercounts: it finds only the sessions
--     that touched a control, not those that landed and left.


-- =====================================================================
-- 2026-08-02  timestamp was UTC for EVERY row written before this date
-- =====================================================================
-- NO DDL REQUIRED. Recorded because it changes how an existing column
-- must be read, which no schema diff will ever show you.
--
-- All five tables stamp `timestamp` with now_local() -- the visitor's own
-- local time, by design, so a timestamp means something to the person who
-- generated it. The timezone behind that came from a JS round-trip:
-- detect the zone, write it into the URL with history.replaceState, click
-- a hidden button to force a rerun that would pick the new param up.
--
-- It never worked. replaceState changes the browser's address bar without
-- telling the server, and Streamlit sends the query params the frontend
-- captured at PAGE LOAD -- so the rerun read exactly what it read before.
-- The address bar showed the right value the entire time, which is why it
-- survived two rounds of fixing. From this date the zone comes from
-- st.context.timezone, which arrives with the initial connection.
--
-- Reading this later:
--
--   * Every `timestamp` written BEFORE 2026-08-02 is UTC, whatever zone
--     the visitor was in. Not "usually UTC" -- always, since the
--     mechanism could not succeed. Rows at +00:00 are not a cluster of
--     UK/Iceland traffic.
--
--   * CORRECTED 2026-08-03, having been overstated here. Every row is a
--     correct ABSOLUTE INSTANT in both eras: now_local().isoformat()
--     always emits an offset, "+00:00" before the fix and the visitor's
--     real zone after. Normalise with pd.to_datetime(..., utc=True) and
--     daily/weekly buckets are valid straight ACROSS this date. The
--     earlier claim that pre-fix rows "push evening usage onto the
--     following calendar date" is true only of the naive wall-clock text,
--     not of a parsed timestamp.
--
--   * What IS lost before this date is the VISITOR'S OWN local time of
--     day. Those rows all say +00:00 wherever the visitor was, and no
--     column records their zone, so "do people use this in the evening,
--     their time?" is answerable only from this date onward. The instant
--     is fine; the wall clock beside it is not.
--
--   * Bucket in a NAMED zone, not UTC. app.py's TRAFFIC_REPORT_TZ
--     (America/Los_Angeles) exists for this: .dt.date straight off UTC
--     files a 20:00 Pacific visit under the following day, which for a
--     US audience misplaces roughly every evening session. Correct
--     instants, wrong bucket.
--
--   * `timestamp` is a TEXT column (see README.md), not timestamptz, so
--     Postgres normalises nothing and any SQL "ORDER BY timestamp" or
--     "timestamp < '...'" is a LEXICOGRAPHIC string comparison. That was
--     safe only while every row carried +00:00, and has not been since
--     this date. Parse in pandas, or compare on a substring you know is
--     zone-stable.
--
--   * Ordering is unaffected -- UTC is monotonic. The existing guidance
--     to order scenario_events by event_seq rather than timestamp still
--     holds, for the separate reason that post-fix timestamps come from
--     the visitor's clock and can tie or move backwards.


-- =====================================================================
-- 2026-08-02  usage_logs.action gains "pageview_schools"
-- =====================================================================
-- NO DDL REQUIRED, same as the pageview_repayment note above. Recorded
-- for the same reason: the schema did not change but the data's meaning
-- did.
--
-- The budget school search got its own standalone page at ?tool=schools,
-- which logs "pageview_schools" rather than a plain "pageview".
--
-- Reading this later:
--
--   * "pageview" narrowed a SECOND time on this date. It now means
--     "calculator landing" only -- not repayment, not schools. Both
--     narrowings happened the same day, so a single cut at 2026-08-02
--     separates the old pooled meaning from the current split one.
--
--   * Total traffic is now THREE actions. Prefer a prefix match --
--         action like 'pageview%'
--     -- over an enumerated list, so a future tool page is counted
--     without anyone remembering to update the query. app.py exposes
--     PAGEVIEW_ACTIONS, derived from STANDALONE_TOOLS for the same
--     reason; analyze_survey.py uses the prefix form.
--
--   * The survey rate still uses bare "pageview" alone, deliberately.
--     No standalone tool page renders the survey, so including those
--     visits would divide by people who were never asked.
--
--   * Pre-split school-search visits are not recoverable as landings,
--     but unlike the repayment case there IS a partial proxy: the module
--     logged "school_search_run:..." from the day it shipped. That finds
--     sessions that ran a search, not sessions that landed and left, so
--     it undercounts -- but it is a real lower bound, which the
--     repayment module has no equivalent of.


-- =====================================================================
-- 2026-08-03  "2026 Federal Repayment Plans" module removed
-- =====================================================================
-- NO DDL. Same treatment as the Trade Apprenticeship removal above: the
-- columns stay, they simply stop being written. Dropping them would
-- destroy the history they already hold.
--
-- Columns now frozen (last written 2026-08-02):
--     future_forecasting_active
--     future_plan_selected
--     scenario_b_future_plan_selected
-- on survey_responses, pdf_downloads, scenario_shares and scenario_events.
--
-- WHY IT WENT. It was an optional Advanced Analysis module that compared
-- RAP against the Tiered Standard Plan. Both are now ordinary entries in
-- the main Repayment Strategy dropdown -- RAP is the DEFAULT for 2026+
-- start years -- so the module compared two plans the visitor could
-- already select, using a second code path (compute_future_plan_result)
-- that had to be kept in step with calculate_roi by hand. That second
-- path is exactly where hs_wage_index went missing and put a 76%
-- overstatement on screen. Removing it removes the drift surface.
--
-- Reading this later:
--
--   * future_forecasting_active IS NULL means one of two different
--     things and they cannot be told apart after this date: the visitor
--     did not enable the module (before), or the module did not exist
--     (after). Condition on the date, not on the column.
--
--   * A TRUE in that column means the visitor opted into a side-by-side
--     RAP/Tiered comparison. It does NOT mean they were repaying under
--     RAP -- that is repayment_strategy, which is independent and is the
--     column any plan-choice analysis wants.
--
--   * ?future= is no longer emitted or read. Old share links carrying it
--     still load; the param is ignored rather than erroring.


-- =====================================================================
-- 2026-08-03  usage_logs.action gains "nav:from=X:to=Y"
-- =====================================================================
-- NO DDL. usage_logs has four columns and `action` is the only free-text
-- one; this file's own earlier entries state the convention that new
-- event types go there rather than into a column.
--
-- Emitted once per landing that followed one of the app's own internal
-- links, alongside (never instead of) the pageview action. X and Y are
-- "calculator" or a STANDALONE_TOOLS key, validated against NAV_ORIGINS
-- before the row is written -- an unvalidated ?from= would let a
-- hand-edited URL inject arbitrary text into the research dataset.
--
-- One variant carries a third segment: "nav:from=schools:to=calculator
-- :inpage=1" is the "Use this school" handoff, which is a rerun rather
-- than a navigation and so produces NO pageview.
--
-- Reading this later:
--
--   * It is a SEPARATE event, not a suffix on the pageview action, and
--     that is load-bearing. Five readers match landing actions by whole
--     string -- the admin landing metrics, the traffic-by-source table
--     and analyze_survey's survey-rate denominator -- so a suffix would
--     have silently zeroed every landing count.
--
--   * The event:k=v shape is the one analyze_survey.py already parses
--     generically, so `nav` arrives there as a named event with `from`,
--     `to` and `inpage` as columns, with no parser to write.
--
--   * COLD ARRIVALS ARE A FLOOR, NOT A COUNT. The admin panel derives
--     them as tool pageviews minus inbound page navigations. A visitor
--     who copies a URL out of their address bar passes ?from= on to
--     whoever they send it to, and that recipient is then counted as
--     internal. The error is bounded and one-directional: it can only
--     understate cold arrivals, never overstate them.
--
--   * DO NOT subtract in-page handoffs. They have no matching pageview,
--     so including them can drive the cold figure negative.
--
--   * Nothing here links one visitor across pages. A cross-link starts a
--     new Streamlit session with a new session_id, and the originating
--     session_id is deliberately NOT carried: a copied URL would then
--     make two people look like one journey, which is the same class of
--     fabricated linkage that keeps ?src= out of share links. These are
--     transition COUNTS and cannot answer "what did this visitor do
--     next".
--
--   * Transitions exist only from 2026-08-03. Before that the calculator
--     had no links to either tool at all -- both were in-page expanders
--     -- so main->tool navigation did not merely go unrecorded, it could
--     not happen.

-- ---------------------------------------------------------------------------
-- 2026-08-08 -- cc_mode_a / cc_mode_b gain a fourth value: 'ccb'
-- ---------------------------------------------------------------------------
-- NO SCHEMA CHANGE. Both columns store the raw radio string, so a new mode
-- rides on the existing text column exactly as 'associate' did.
--
-- What it means: the visitor modelled a bachelor's degree awarded BY the
-- community college itself (a community college baccalaureate), rather than a
-- 2+2 transfer where the four-year school confers the degree. It is offered
-- only when the selected school predominantly awards sub-bachelor's
-- credentials AND awards at least one bachelor's (see ccb_school in app.py),
-- so its presence in a row is also a statement about the school.
--
-- Why it matters for analysis: unlike every other cc_mode, this one FINANCES
-- the community-college years. So within cc_mode != 'none', loan amount and
-- personal_contribution mean different things for 'ccb' rows than for the
-- other three -- the others push community-college cost into personal
-- contribution and out of the loan, and this one does the reverse. Do not
-- pool 'ccb' with the transfer modes when comparing borrowing.
--
-- Rows before this date cannot contain 'ccb'. Its absence in an earlier row is
-- "the option did not exist", not "the visitor declined it".


-- =====================================================================
-- 2026-08-09  school_search_run undercounts from 2026-08-02 to 2026-08-09
-- =====================================================================
-- NO DDL. Nothing to paste into the SQL editor. This records a gap in
-- usage_logs that no column and no row can show you, because the rows
-- that would show it are the ones that were never written.
--
-- WHAT HAPPENED. render_school_search only logs a search once the visitor
-- has actually touched a control -- search_was_adjusted() intersects the
-- set of interacted keys with SEARCH_CONTROL_KEYS. On 2026-08-02 the cost
-- control changed from a single "most I could pay" slider to a range, and
-- its widget key changed with it, `search_budget` -> `search_coa_range`.
-- SEARCH_CONTROL_KEYS kept the old spelling.
--
-- So for that window the intersection could never match on the budget:
-- a visitor who dragged the cost range and changed nothing else was
-- treated as not having searched, and wrote no school_search_run row.
-- Nothing else broke. The control worked, the results rendered, the
-- visitor saw the right schools -- only the log went quiet.
--
-- WHAT IT COSTS THE ANALYSIS. The undercount is NOT uniform, so it cannot
-- be corrected with a scale factor:
--
--   * It only ever dropped searches whose ONLY adjustment was the budget.
--     A visitor who also picked a field, a state or a level still logged,
--     because those keys stayed correct throughout.
--
--   * Budget is the control the feature is named for, so the missing
--     searches are biased toward the purest use of it -- someone who
--     accepted the prefilled field of study and moved only the price.
--     In Major mode the field IS prefilled, which makes budget-only the
--     natural path rather than an unusual one.
--
--   * Direction is one-way: school_search_run in this window is a floor
--     on searches, never a ceiling. Treat a low count as "at least this
--     many", and do not compare the window's search RATE against either
--     side of it.
--
-- Sessions are unaffected as sessions -- a dropped search does not drop
-- the pageview, the scenario_events or anything else that visit wrote.
-- It is specifically the "did they run a search" flag that is missing,
-- so a funnel built on it has a broken middle step and intact ends.
--
-- The window is bounded on both sides. Before 2026-08-02 the key matched
-- and budget drags logged; from 2026-08-09 it matches again. Note the
-- earlier boundary too: the adjusted-search gate itself only landed on
-- 2026-08-01 (before that every session logged a search on page load, so
-- school_search_run meant "loaded the page" -- see the note on that date).
-- Three regimes in nine days; cut on both dates, not just one.


-- =====================================================================
-- 2026-08-09  A THIRD standalone page: pageview_gradschools
-- =====================================================================
-- NO DDL. usage_logs.action is free text and a new event type goes there
-- rather than into a column -- the convention this file has followed since
-- the repayment page.
--
-- WHAT CHANGED. The graduate school search moved out of the schools page
-- into its own tool, so landings are now FOUR actions:
--
--     pageview               the calculator, and only the calculator
--     pageview_repayment     ?tool=repayment
--     pageview_schools       ?tool=schools   (undergraduate only from today)
--     pageview_gradschools   ?tool=gradschools
--
-- Use a prefix match -- action like 'pageview%' -- for total traffic. The
-- 2026-08-02 entry above already asked for this so that a future tool would
-- be counted without anyone remembering to update a query; this is that
-- future tool, and any enumerated list written before today is now short by
-- one.
--
-- The survey rate still divides by bare "pageview" alone, deliberately: no
-- standalone tool renders the survey, so including these visits would divide
-- by people who were never asked.
--
-- CUT DATES NOW STACK. The admin caption's "landings split cleanly only from
-- 2026-08-02" gains a second boundary here. Before today there was no
-- gradschools page at all, so its absence in an earlier row is "the page did
-- not exist", not "nobody visited it" -- the same reading the ccb entry above
-- asks for.
--
-- school_search_run IS UNCHANGED, AND THAT IS DELIBERATE. Both tools still
-- emit that one event; `level` separates them (bachl/assoc/cert* against
-- master/doctoral), exactly as it did while the graduate levels lived inside
-- the schools page. A separate event name would have created a FOURTH regime
-- on top of the three that series already has (see the 2026-08-09 entry on
-- the budget-only undercount), and analyze_survey.py already groups runs by
-- level. So a search is comparable across today's split; only where the
-- visitor was standing changed.
--
-- One asymmetry worth knowing before comparing the two halves: a graduate
-- search can now be reached WITHOUT a schools pageview, and before today it
-- could not. Any funnel that used pageview_schools as the denominator for
-- graduate searches is measuring something that stopped existing.

-- =====================================================================
-- 2026-08-10 -- WRITES CAN NOW BE DROPPED, AND THE GAP IS NOT RANDOM.
-- No DDL. This records a change in how rows ARRIVE, which matters more
-- for reading the data than most schema changes do.
--
-- Until today every writer ran synchronously inside the page render, with
-- the PostgREST client's own 120-SECOND default timeout. A slow database
-- therefore did not lose rows -- it held the page open until the row
-- landed. Under load that is a hang, so it was fixed: the timeout is 3s,
-- and usage_logs + scenario_events now go through a bounded in-process
-- queue with a circuit breaker.
--
-- The consequence for analysis: those two tables can now LOSE rows, and
-- they lose them in the worst possible pattern -- exactly when the
-- database is struggling, which is exactly when traffic is highest. A
-- quiet hour and a shed hour are the same shape in this data.
--
--   * survey_responses, pdf_downloads and scenario_shares are UNAFFECTED.
--     They are still written synchronously (with the 3s timeout), because
--     each one shows the visitor a confirmation. A row missing from those
--     three still means what it always meant.
--   * usage_logs and scenario_events may be short. The admin page reports
--     written / failed / dropped / skipped counts since the last container
--     restart -- there is no persistent record, because a process that is
--     being restarted cannot write one.
--   * If a spike is ever visible in the traffic digest, treat the same
--     window in these two tables as a LOWER BOUND, and record the window
--     here by hand. That is the only durable trace there will be.
--
-- Before this date, absence in usage_logs means "did not happen". After
-- it, absence means "did not happen, or was shed". Do not pool the two
-- eras when computing a rate whose denominator is a usage_logs count.

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

-- Expected: DELETE 5, leaving the 5 genuine responses from 2026-07-12..14.
-- Those five predate any IRB determination and are the pre-approval rows noted
-- below; they are a separate decision and are deliberately NOT deleted here.

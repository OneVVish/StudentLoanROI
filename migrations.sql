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

-- 2026-08-11: THE FRONT DOOR MOVED. From this date the edge Worker serves the
-- static landing page on a bare, parameter-less "/" (and on /welcome); the
-- Streamlit app -- and therefore every `pageview` row -- is reached only via a
-- URL carrying at least one query parameter (the landing's own CTAs append
-- ?go=1; share links, ?src= and ?tool= links always carried params).
-- CONSEQUENCE: `usage_logs` pageview rows before this date are RAW ARRIVALS;
-- rows from this date on are CLICKED-THROUGH visitors, one funnel step deeper.
-- Do not compare pageview counts across this seam without saying so; the
-- landing page itself writes no rows anywhere (it is static, served from the
-- edge).

-- ---------------------------------------------------------------------------
-- 2026-08-11 -- DATA QUALITY NOTE, no DDL.
--
-- Browser verification of the edge deploys wrote a small number of UNTAGGED
-- rows to production, in two known windows:
--
--   * 2026-08-10 ~16:52 local: two page loads of worthmydegree.com/ while
--     verifying the /static edge-cache deploy (the og/preview work). Each
--     load wrote one `pageview` to usage_logs and one scenario_events row.
--   * 2026-08-11 ~17:52 local: one click-through from the bare landing page
--     to /?go=1 while verifying the front-door deploy. One `pageview`, one
--     scenario_events row. (The bare landing page itself logs nothing; the
--     click did.)
--
-- Total: ~3 pageview rows and ~3 scenario_events rows, none carrying a
-- traffic_source -- indistinguishable from organic visits after the fact,
-- exactly the failure mode the 2026-07-31 note describes. All OTHER
-- verification visits in both windows carried ?src=selftest and are
-- self-identified; survey_responses, pdf_downloads and scenario_shares took
-- no rows from any verification visit.
--
-- Guidance: the affected windows are one minute each and the row counts are
-- known and tiny, so no filtering is warranted -- but any analysis counting
-- untagged sessions on 2026-08-10 or 2026-08-11 overstates organic traffic
-- by up to 2 and 1 sessions respectively. Nothing else is affected.
--
-- Process note: production verification MUST carry ?src=selftest on the
-- FIRST navigation -- a tag added on a later navigation does not reach back.
-- The 08-11 row happened because the bare landing page cannot be loaded
-- with a tag without defeating the very routing being tested; the honest
-- sequence is bare load (logs nothing), then hand-append ?src=selftest
-- before clicking through. Recorded so the next verifier does that.

-- ---------------------------------------------------------------------------
-- 2026-08-11 -- NEW ACTION in usage_logs, no DDL.
--
-- The edge Worker (infra/worker.js) now writes landing-page views directly:
--     action        = 'landing_view:path=root' | 'landing_view:path=welcome'
--     session_id    = NULL   (an edge landing has no Streamlit session)
--     traffic_source= the ?src= tag, or NULL
--     timestamp     = UTC (toISOString), not visitor-local
--
-- These rows are NOT app pageviews and are deliberately excluded from
-- PAGEVIEW_ACTIONS. Any query counting sessions, pageviews or survey rates
-- must filter them out -- app.py does, and check_internal_links.py asserts
-- the separation. They begin only once the Worker's SUPABASE_URL /
-- SUPABASE_ANON_KEY secrets are set, so an absence before that date (or
-- before the secrets were added) means "not being written", not "no traffic".
--
-- SAME DATE, "COLD ARRIVALS" CHANGES MEANING. The welcome page's CTAs now
-- carry ?from=welcome, so the app writes `nav:from=welcome:to=<page>` when a
-- landing click arrives -- a new value in an existing event. The admin
-- panel's Cold column is derived as (tool pageviews - inbound page
-- navigations), so a visitor who reaches a tool via the welcome page counts
-- as INTERNAL from this date where they would previously have counted as
-- COLD. That is more accurate -- they did come from one of our pages -- but
-- it is a seam: do not compare Cold across 2026-08-11 without saying so.
--
-- The derived "No click" figure on that panel is landings minus welcome-origin
-- navs. It is an ESTIMATE, not an observation: a visitor who closes the tab
-- sends nothing. Two leaks, both stated on the panel -- clicking two CTAs
-- counts twice against one landing, and returning later lands twice.

-- ---------------------------------------------------------------------------
-- 2026-08-11 -- ONE TAGGED VERIFICATION ROW, no DDL.
--
-- Verifying the edge landing logger immediately after its deploy wrote one
-- row to usage_logs:
--
--     timestamp     2026-08-11T18:52:42Z
--     action        'landing_view:path=welcome'
--     traffic_source'deploycheck'
--     session_id    NULL
--
-- It is TAGGED, so unlike the untagged rows noted above it can be excluded
-- exactly: traffic_source = 'deploycheck'. Recorded anyway so the tag is
-- never mistaken for a real channel if it turns up in a source breakdown.
-- 'deploycheck' is reserved for this purpose and appears in no marketing
-- link.
--
-- WHY IT COULD NOT USE THE USUAL 'selftest' TAG: the Worker drops
-- src=selftest (and ?test=1) BEFORE building the row, by design -- which
-- also means the landing logger cannot be verified with it. A distinct
-- tag is the honest way to prove the path works: it writes a real row and
-- labels itself. If the logger ever needs re-verifying, reuse
-- 'deploycheck' rather than visiting untagged.
--
-- Two rows in the same minute (18:51:29 and 18:52:05, action
-- 'landing_view:path=root', no tag) are NOT verification traffic -- they are
-- organic visitors who typed the domain, and are genuine data.

-- ---------------------------------------------------------------------------
-- 2026-08-11 -- TWO MORE EDGE ACTIONS in usage_logs, no DDL.
--
-- The Worker now also serves guide articles, and writes:
--     'guide_view:slug=<slug>'    a guide page was served ('index' = /guides)
--     'article_like:slug=<slug>'  the Helpful button was tapped
-- both with session_id NULL and a UTC timestamp, like landing_view.
--
-- NEITHER IS A MEASUREMENT OF PEOPLE, and analysis must not treat them as one:
--   * a guide_view has no session, so reads cannot be de-duplicated to
--     visitors -- one person reloading is two rows;
--   * an article_like has no identity whatsoever. The browser guard is
--     localStorage, which anyone can clear, and the endpoint is public and
--     unauthenticated. It is a warm signal for editorial decisions, nothing
--     more. Do not put it in the paper.
--
-- All three edge actions (landing_view, guide_view, article_like) are kept out
-- of PAGEVIEW_ACTIONS; app.py filters them from every app-activity panel and
-- check_internal_links.py asserts the separation. Any query counting sessions,
-- pageviews or survey rates must exclude EDGE_ACTION_PREFIXES.

-- ---------------------------------------------------------------------------
-- 2026-08-12 -- VISITOR-FACING COPY WAS REWRITTEN APP-WIDE, no DDL.
--
-- No column changed and no row is invalid. This is a WORDING seam: from this
-- date the sentences a visitor reads are not the sentences earlier visitors
-- read, on the calculator, the sidebar, the repayment tool, both school
-- searches, the Methodology footer and the edge-served welcome page.
--
-- What actually changed, and what did not:
--   * PUNCTUATION AND EMPHASIS, mostly. Every em dash in a rendered string
--     became a period, comma or colon, and mid-sentence boldface in the
--     Methodology dropped from 201 spans to 137. Sentences were split and
--     rejoined around those edits.
--   * The Methodology footer gained 15 section and 50 subsection headings. It
--     previously had none; the whole 11,000 words ran as one flat block.
--   * NO FIGURE MOVED. Every dollar amount, percentage, URL, SOC code and year
--     in the Methodology was diffed against the previous revision and is
--     identical. Nothing in section 1's constants, no simulator, no ROI path
--     was touched, so every logged earnings_premium, roi_pct, monthly_payment
--     and payoff figure means exactly what it meant on 2026-08-11.
--
-- WHY THIS IS RECORDED AT ALL. The paper's H1/H2 outcomes are survey answers
-- and behaviour, both of which respond to wording. A visitor on 2026-08-13 was
-- asked to act on differently-phrased captions than one on 2026-08-10, so a
-- comparison spanning this date is not measuring the same instrument twice.
-- Condition on the date, or say in the write-up that copy was held constant
-- only within each side of it.
--
-- THE EXPERIMENT ARMS ARE UNAFFECTED RELATIVE TO EACH OTHER. Both result
-- branches were edited identically, deliberately -- the single-scenario and
-- Compare Mode copies of each string moved together, and the shared helpers
-- (breakeven_summary, counterfactual_vocab, render_takehome_block) are shared
-- by construction. So H2's contrast between arms is preserved across the seam;
-- what shifts is the common baseline both arms sit on. A between-arms
-- comparison is safe to pool; a before/after comparison of either arm alone is
-- not.
--
-- THE RESEARCH INSTRUMENT ITSELF WAS NOT TOUCHED, on purpose. The pre-survey
-- question text, PRESURVEY_BORROWING_OPTIONS and the exit survey were left
-- exactly as they were, dashes and all, precisely so that this seam does not
-- reach the questions. Their stored codes (s0..s5plus, n0, b1..b5) are
-- unchanged and remain comparable across the whole series. If that text is
-- ever reworded, THAT needs its own entry here and it is a much harder seam
-- than this one.
--
-- ALSO ON THIS DATE, and harmless to analysis: the PDF/Share/Image buttons
-- took the brand's orange (a CSS change, no new event, no key renamed, so
-- pdf_download and scenario_share rows are unaffected), and four PDF section
-- labels were brought back into step with their on-screen twins. Any
-- click-rate comparison on those three buttons across this date is measuring
-- a colour change as well as anything else.
--
-- Edge half went live 2026-08-12 (Worker version a721cf27); the app half
-- lands whenever PR #44 merges and Streamlit Community Cloud redeploys, so
-- the app-side seam is the MERGE date, not this one. Fill it in when known.

-- ---------------------------------------------------------------------------
-- 2026-08-13 -- A FOURTH EDGE ACTION in usage_logs, no DDL.
--
-- Every guide gained a Share button, beside Helpful at the foot of the
-- article, and the Worker writes:
--     'article_share:slug=<slug>'   the link left the page
-- session_id NULL, UTC timestamp, traffic_source from ?src= -- the same shape
-- as landing_view / guide_view / article_like, and no column changed.
--
-- IT IS THE SOFTEST SIGNAL IN THIS DATABASE. Read the qualifiers before using
-- the column for anything:
--   * a row is written when navigator.share RESOLVES or the link reaches the
--     clipboard. NEITHER PROVES DELIVERY. Some browsers resolve the share
--     sheet on invoke rather than on send, and a copied link can be pasted
--     nowhere;
--   * a cancelled share sheet (AbortError) writes NOTHING, deliberately, and
--     neither does the last-resort branch that reveals the link on the page --
--     so the count under-reports as well as over-reporting, in different
--     directions, and the two do not cancel;
--   * there is NO dedupe of any kind. article_like at least has a localStorage
--     guard; this has none, because sharing a guide with two people twice is a
--     real act and nothing on the client can tell it from a fidget. One reader
--     can be many rows;
--   * the endpoint is public and unauthenticated, like the like endpoint.
--
-- So: an UPPER BOUND on intent to pass a guide on. Never a count of people,
-- never a rate against guide_view, and not in the paper.
--
-- SHARES BEFORE THIS DATE DO NOT EXIST AS ZEROES -- there was no button. A
-- per-guide comparison across this date is comparing a feature's absence with
-- its presence, and the two older guides (for-parents-run-the-numbers,
-- parent-plus-senior-year) have accumulated reads since 2026-08-11 with no
-- share control on the page at all.
--
-- SHARE_ACTION_PREFIX joins EDGE_ACTION_PREFIXES, so every existing exclusion
-- (pageview rates, survey denominators, app-activity panels) picks it up
-- automatically. check_internal_links.py asserts app.py and the Worker still
-- agree on the string, and that the article pages POST to the route the
-- Worker answers on -- a rename on one side alone would leave the admin
-- column reading zero, which is exactly what an unshared guide looks like.
--
-- ALSO ON THIS DATE, harmless to analysis: the site footer changed from
-- "Built from ..." to "Source: ...", and the Share button was right-justified
-- within the reactions bar. No event, no key, no column.
--
-- SAME DATE, TWO FIXES TO THE REACTION ENDPOINTS -- both pre-dated the Share
-- button and both applied to article_like from 2026-08-11. No DDL.
--
-- 1. traffic_source WAS ALWAYS NULL ON EVERY article_like ROW, and the NULL
--    does not mean "organic". The page POSTed to a bare /api/like, so the
--    Worker's url.searchParams.get("src") had nothing to read -- CARRY_QS_JS
--    rewrites <a href> only, never a fetch. The tag was being read off the
--    wrong URL, not dropped by the visitor.
--
--    The article pages now POST to /api/like and /api/share WITH
--    location.search, so from this date a reaction carries the same tag its
--    guide_view does. DO NOT read a pre-2026-08-13 like as untagged traffic,
--    and do not pool the two eras when asking which channel a reaction came
--    from -- every earlier row is structurally NULL. Reads (guide_view) are
--    unaffected and were tagged correctly throughout.
--
-- 2. THE REACTION ENDPOINTS IGNORED ?test=1 AND src=selftest. That check
--    lives in logEdgeView, which handleLike never went through, so tapping
--    Helpful while verifying production wrote a real, untagged row -- the
--    contamination CLAUDE.md records for pageviews, arriving in a per-guide
--    count instead. excludedFromLogging() is now shared by logEdgeView,
--    handleLike and handleShare, and also refuses declared crawlers.
--
--    An excluded tap returns EXACTLY what a real one does (the like count is
--    still read and returned), so the button being verified still behaves as
--    it ships. The reads are deliberately not excluded: a count writes
--    nothing, and blanking it in test mode would hide the thing being checked.
--
--    Nothing can be done about likes already recorded this way. There are few,
--    they are per-guide counts rather than research data, and they cannot be
--    told apart from real ones -- the same irreversibility as the untagged
--    pageviews of 2026-07-30/31.

-- ---------------------------------------------------------------------------
-- 2026-08-14 -- SIX PROFESSIONAL OCCUPATIONS GAINED THEIR TRAINING STRUCTURE.
-- No DDL. Every stored figure for these paths changes meaning.
--
-- Dermatologists, General Internal Medicine Physicians, Pediatricians General,
-- Physicians All Other and Surgeons All Other were absent from PHYSICIAN_TITLES,
-- and Orthodontists from DENTIST_TITLES. Because PROFESSIONAL_PROGRAM_BY_OCCUPATION
-- is derived from those lists, all six were also absent from it, so until this
-- date each was:
--
--   * charged nine years of school with NO medical or dental school debt,
--   * paid a full specialist salary from the year after a bachelor's,
--   * given a federal professional cap of 0, which split_loan_financing reads
--     as a real cap rather than "unset".
--
-- The same omission as Dentists, General (2026-08) and Lawyers before it, and
-- this time it included the two largest physician occupations in OEWS. On a
-- $182,476 undergraduate loan a Dermatologist read a principal of $182,476,
-- $139,990 of income in year zero and a 12.1 year payoff. It now reads
-- $387,476, $0 and 27.6 years, which is what the twelve modelled specialists
-- beside it always read.
--
-- ANY ROW NAMING ONE OF THESE SIX IS NOT COMPARABLE ACROSS THIS DATE.
-- scenario_events, survey_responses, pdf_downloads and scenario_shares all
-- store scenario_a_major / scenario_b_major, and roi_pct, earnings_premium,
-- monthly_payment, payoff_years and dti_ratio all move for them. Rows before
-- this date describe a path priced as if the degree were free. Condition on
-- the date or exclude the six titles; do not pool.
--
-- Everything else is untouched: the twelve physicians, four dentists and the
-- law, pharmacy, veterinary, optometry, podiatry and chiropractic paths already
-- carried the structure and are bit-identical across this change.
--
-- UNMODELLED_DOCTORAL_TITLES now lists the 42 doctoral occupations the app
-- deliberately does not model as professional, and
-- check_school_search_filters.py fails if a doctoral occupation belongs to
-- neither set. That is what makes a fourth recurrence impossible rather than
-- unlikely: the previous checks all started from the map, so a title missing
-- from it was invisible to every one of them.
--
-- STILL WRONG, IN THE OTHER DIRECTION, and tracked rather than fixed: those 42
-- are charged nine years of tuition with no stipend. For a funded research PhD
-- that overstates cost by five years of a school's published price, and the
-- clinical doctorates in the list (Audiologists, Clinical and Counseling
-- Psychologists, Physical Therapists) must not be lumped in with them, because
-- those generally are self-funded.

-- ---------------------------------------------------------------------------
-- 2026-08-14 -- DENTISTRY'S NATIONAL DEBT FIGURE WAS THE WRONG DEFINITION.
-- $293,900 -> $279,900. No DDL. Same day as the six-physician fix above and
-- independent of it.
--
-- additional_training_debt is added ON TOP of the undergraduate loan the app
-- charges, so it must be graduate borrowing only. The Scorecard per-school
-- figures are scoped exactly that way and were always correct. The national
-- FALLBACK was not: ADEA's "average education debt" is defined as dental
-- school borrowing PLUS predental debt, "the outstanding education debt the
-- senior students had when they entered dental school", so the bachelor's was
-- counted twice for anyone who did not name a school.
--
-- The replacement is derived, because ADEA publishes the split and not the
-- dollar: Dentists of Tomorrow 2025 gives a mean of $297,800 among indebted
-- graduates and puts dental school loans at 94% of it against 6% predental,
-- so 0.94 x $297,800 = $279,932, to the hundred. Same report, same class, so
-- no vintage is mixed; the published 94% is rounded, which puts the result
-- within about $1,500.
--
-- WHO IS AFFECTED. Only scenarios on a dentistry path that did NOT name a
-- dental school: the five DENTIST_TITLES occupations and the curated
-- "Dentistry" entry. Naming a school has always used that school's Scorecard
-- figure and is unchanged. Medicine and law are untouched, because AAMC's
-- cited figure is medical-school-only (its premedical debt is reported
-- separately, $28,000 median for the Class of 2025) and the ABA figure is law
-- school debt.
--
-- The national private tranche for dentistry moves with it: $93,900 above the
-- $200,000 federal professional ceiling becomes $79,900. Any stored
-- roi_pct, earnings_premium, monthly_payment, payoff_years or dti_ratio for an
-- unnamed-school dentistry scenario is not comparable across this date.

-- ---------------------------------------------------------------------------
-- 2026-08-14 -- PROFESSIONAL DEGREES WERE PAID FOR TWICE IN DETAILED MODE.
-- No DDL. Third entry on this date and the largest of them.
--
-- additional_training_debt is added ON TOP of the undergraduate loan, so the
-- cost model has to stop at the undergraduate years on a path that carries
-- one. It did not. compute_loan_schedule_by_year was called with the WHOLE
-- programme length, so a dermatologist at a $45,619 school was charged eight
-- years of that COA ($364,952) and then had $205,000 of medical school debt
-- added, for a $569,952 principal where the twelve already-modelled physician
-- titles read $387,476.
--
-- Both halves looked right in isolation, which is why it survived: the cost
-- model charged the length BLS says the path takes, and the debt is the figure
-- AAMC publishes. What settles which gives way is Scorecard's own scoping,
-- already quoted in build_professional_debt.py -- its per-school debt "only
-- includes loans disbursed at the same academic level as the evaluated
-- credential level", i.e. the undergraduate loan is charged separately, which
-- only works if the undergraduate loan is undergraduate.
--
-- school_cost_years() is the rule, and check_school_search_filters.py asserts
-- it in BOTH directions: a professional path must not be charged its graduate
-- years, and a master's must still be, since nothing else pays for those and
-- trimming them prices the degree at zero.
--
-- WHO IS AFFECTED. DETAILED MODE ONLY, on the ~30 occupations carrying
-- additional_training_debt (the physician, dentist and law titles plus
-- pharmacy, veterinary, optometry, podiatry and chiropractic). Simplified mode
-- passes federal_cap=None, skips the split entirely and never built a cost
-- schedule, so it is bit-identical. Master's paths and the 42 research and
-- clinical doctorates in UNMODELLED_DOCTORAL_TITLES are bit-identical too.
--
--   Dermatologists   $569,952 -> $387,476
--   Dentists, General $658,852 -> $462,376   (with the ADEA fix above)
--   Lawyers          $449,190 -> $312,476
--
-- The federal cap moved with it: graduate_direct_cap now contributes 0 on
-- these paths, because that borrowing is professional_debt_cap's job and
-- counting both gave the undergraduate pool $82,000 of graduate capacity it
-- does not have. The tranche split therefore changes as well as the total.
--
-- Any stored roi_pct, earnings_premium, monthly_payment, payoff_years or
-- dti_ratio from a DETAILED-mode scenario on one of those occupations is not
-- comparable across this date. loan_mode is stored, so the affected rows can
-- be isolated exactly rather than by excluding the occupations wholesale.

-- ---------------------------------------------------------------------------
-- 2026-08-14 -- RESEARCH DOCTORATES ARE NOW MODELLED AS FUNDED. No DDL.
-- The largest modelling change of the day: it moves BOTH sides of the ROI for
-- 39 occupations, so their rows change meaning more than any fix above.
--
-- Until now every occupation BLS files as "Doctoral or professional degree"
-- that was not a professional path was charged nine years of an undergraduate
-- school's cost of attendance AND paid a full salary from the year after a
-- bachelor's. Both halves were wrong, in opposite directions. A funded PhD pays
-- no tuition and earns a stipend.
--
--   COST:     the 5 doctoral years are no longer charged. At a $45,619 school
--             that is $228,095 removed from the modelled cost.
--   EARNINGS: those years now earn PHD_STIPEND ($28,788, NIH's FY2025
--             Kirschstein-NRSA predoctoral level) instead of the full
--             occupational salary.
--
-- THE SECOND EFFECT IS LARGER THAN THE FIRST AND RUNS THE OTHER WAY, which is
-- why no row is safe to pool. History Teachers, Postsecondary at a $45,619
-- school went from a 10-year premium of +$149,215 to -$68,497: the cost fell
-- by $228,095 and five years of professor's salary were replaced by five years
-- of stipend. A path that looked comfortably worth it now does not, inside a
-- 10-year window. Any comparison across this date on these occupations is
-- measuring the fix, not the world.
--
-- WHICH OCCUPATIONS. RESEARCH_DOCTORATE_TITLES, 39 of them: the 35
-- postsecondary teaching titles plus Astronomers, Physicists, Biochemists and
-- Biophysicists, and Medical Scientists.
--
-- WHICH ARE DELIBERATELY EXCLUDED, and still charged nine years:
-- Audiologists (AuD), Clinical and Counseling Psychologists (PsyD) and
-- Physical Therapists (DPT). Those are clinical practice doctorates students
-- generally pay for, and marking them funded would swap the old error for its
-- mirror image. They are the whole of UNMODELLED_DOCTORAL_TITLES now.
--
-- THE MAJORITY PATH IS MODELLED AND THE MINORITY IS DISCLOSED, the same call
-- the app already makes for underemployment and optional residencies. NSF's
-- Survey of Earned Doctorates: ~33% research assistantship or traineeship, 24%
-- fellowship, 22% teaching assistantship, against 15% primarily own resources.
-- Debt is field-dependent -- 72%+ finish with none in the sciences and
-- engineering, about half in psychology, the social sciences and humanities --
-- and funded_doctorate_disclosure() says so on screen, because the humanities
-- teaching titles are most of this list.
--
-- Professional paths, master's paths and every bachelor's-level occupation are
-- bit-identical across this change.


-- ===========================================================================
-- 2026-08-14  count_foregone_earnings DEFAULTS TO TRUE.  NO DDL.
-- ===========================================================================
-- The "Count foregone earnings during enrollment" option shipped OFF by
-- default and is now ON. This is a SEAM IN THE RESEARCH DATA, not a UI tweak.
--
-- The column is written on every scenario row (build_scenario_context) and it
-- defines what earnings_premium and roi_pct MEAN. With it off, the clock starts
-- at graduation and only tuition and debt are charged against the degree. With
-- it on, the clock starts at age 18 and the counterfactual is credited with the
-- wages earned while the student was enrolled. The two are different questions
-- and the numbers are not comparable:
--
--   Dentists, General, Berkeley COA, RAP, 10-year window
--     foregone OFF   earnings premium  +$261,556
--     foregone ON     earnings premium   +$85,541
--
-- The effect scales with programme length, so pooling across this date does not
-- merely add noise -- it biases against long paths, which are exactly the ones
-- the flag moves most.
--
-- ANALYSING ACROSS 2026-08-14 MUST CONDITION ON count_foregone_earnings.
-- Do not treat it as a minor covariate: before this date a TRUE value means the
-- visitor went into the Advanced expander and ticked it, which is a selected,
-- unusually engaged population. After it, TRUE is simply the default and FALSE
-- is the deliberate act. The column's distribution and its meaning as a signal
-- both invert here.
--
-- Rows before the option existed at all carry NULL; treat NULL as false, which
-- is what the pre-2026-07-31 note above already says.
--
-- Why the default changed: the wages given up while enrolled are the largest
-- real cost of a degree, larger than tuition, and omitting them flattered every
-- path -- most of all the long ones, where a dentist was compared against a
-- high school graduate who had somehow not worked for eight years. It stays a
-- switch rather than becoming unconditional (the treatment hs_baseline_age_aware
-- got) because both answers are defensible: "what do I gain from here" is a real
-- question for somebody already enrolled, and that is what OFF now means.


-- ===========================================================================
-- 2026-08-14  professional_debt_a / _b BECOME AN INPUT.  NO DDL.
-- ===========================================================================
-- The professional-school debt was a RESOLVED value (a school's median, a
-- price carried from the graduate search, or a national average) and is now an
-- editable sidebar field seeded from that resolution. build_scenario_context
-- therefore logs the FIELD rather than re-resolving, so the column continues to
-- name the figure every other number in the row was built from.
--
-- Two consequences for anyone reading these columns across this date:
--
--   1. A 0 IS NOW POSSIBLE AND IS NOT A MISSING VALUE. It means the visitor
--      said this degree carries no debt (a scholarship, an employer, the
--      military, family money). NULL still means what it always meant: this
--      path attends no professional school at all. Do not coalesce them.
--      Before this date the app could not produce a 0 here -- resolve_
--      professional_debt is documented never to return one -- so every 0 is
--      after the seam by construction.
--   2. The value is no longer implied by prof_school_a. Before, (major,
--      prof_school) determined the figure and it could be recomputed from a
--      later dataset release. It cannot now, which is the reason the figure has
--      always been stored alongside the name and is the reason to keep doing so.
--
-- Why this changed: entering $0 in Total Loan Amount did not model zero debt on
-- the 33 occupations that attend a professional school. get_effective_principal
-- added the professional figure on top of the slider regardless, so a visitor
-- who typed 0 was shown a $279,900 loan (Dentists), $205,000 (physicians) or
-- $130,000 (Lawyers) with nothing on screen explaining the disagreement. The
-- rows written before this date are correct about what the model did; they are
-- simply drawn from a population that could not express a zero.
--
-- The share link carries the field as ?pdebt= / ?pdebt_b=, so a row's figure is
-- reproducible from a shared scenario the same way the rest of the inputs are.


-- ===========================================================================
-- 2026-08-14  THE SALARY CURVE STOPS AT THE OCCUPATION'S p90.  NO DDL.
-- ===========================================================================
-- get_annual_salary_for_year now flattens at career_earnings_ceiling -- the
-- occupation's own OEWS 90th percentile, for whichever geography the scenario
-- resolved (metro, state or national). Everything downstream of a salary moves
-- with it: earnings_premium, roi_pct, the break-even, the net-position series.
--
-- THE SEAM IS AT roi_horizon_years, NOT AT THE DATE, and it is narrow:
--
--   * horizon 10  -- BIT-IDENTICAL. Zero of the 825 occupations reach their own
--                    p90 by year 10, verified across the whole file by
--                    check_career_stages.py. Rows at the default horizon are
--                    unaffected and need no conditioning.
--   * horizon 15  -- 2 occupations affected.
--   * horizon 20  -- 15.
--   * horizon 30  -- 302, the worst 4.14x over its own p90 before the change.
--
-- So pooling across this date is safe for the 10-year rows that dominate the
-- table, and unsafe for 20- and 30-year rows. Condition on roi_horizon_years,
-- which every row already carries.
--
-- The direction of the correction is one-way: the ceiling can only LOWER a
-- salary, so any long-horizon earnings_premium or roi_pct written before this
-- date is at least as flattering as the same scenario is now.
--
-- Why: the growth rate is solved to climb from the 25th percentile to the
-- MEDIAN over ten years, and compounding it for a career walks out of the data
-- it was fitted to. It was invisible while nothing on the page reached those
-- years; the Year 20/Year 30 take-home stages and the 35-year net-position
-- chart, shipped the same day, are what put it on screen -- as a headline
-- "Gross Salary" of $1,590,753 for a surgeon.
--
-- Two level shifts now scale wage_percentiles with the salary they move
-- (prestige tiers, and a returning student's entered salary), so the ceiling
-- travels with them. Those rows are unaffected at any horizon where the
-- ceiling did not bind before.


-- ===========================================================================
-- 2026-08-14  RAP IS RENAMED "2026 RAP (Repayment Assistance Plan)".  NO DDL.
-- ===========================================================================
-- The label only. Not one number moves: the plan, the simulator, the payment
-- table and the forgiveness clock are untouched, and a scenario re-run either
-- side of this date produces identical figures.
--
-- What changes is a STRING WRITTEN INTO FIVE TABLES. repayment_strategy (and
-- repayment_strategy_b) on survey_responses, pdf_downloads, scenario_shares and
-- scenario_events carries the label by value, so from this date the same plan
-- appears under two spellings:
--
--     'Repayment Assistance Plan (RAP)'         <- before 2026-08-14
--     '2026 RAP (Repayment Assistance Plan)'    <- from 2026-08-14
--
-- ANY analysis grouping by that column must fold them together, or the default
-- plan splits into two arms at an arbitrary date and each looks half as popular
-- as it is. There is no in-database repair: the anon key cannot UPDATE any more
-- than it can DELETE, and rewriting history would destroy the only record of
-- which label a visitor was actually shown. Normalise on read:
--
--     CASE WHEN repayment_strategy IN ('Repayment Assistance Plan (RAP)',
--                                      '2026 RAP (Repayment Assistance Plan)')
--          THEN 'RAP' ELSE repayment_strategy END
--
-- The same fold applies to the repayment tool's own comparison rows, which now
-- take the label from the calculator's constant rather than a second literal.
--
-- Old share links keep working and do NOT write the old string. ?strategy=
-- carries the label by value, and a keyed Streamlit widget RAISES when its
-- stored value is not among its options -- so an unmapped old link would have
-- been a hard error on the recipient's screen, not a quiet fallback.
-- resolve_shared_strategy maps LEGACY_RAP_STRATEGY_LABEL onto the new one, and
-- what gets logged afterwards is the new spelling. A row written after this date
-- therefore says nothing about which link produced it.
--
-- Why rename at all: the two plans a 2026 borrower can actually choose are RAP
-- and the "2026 Tiered Standard Plan". One carried the year and the other did
-- not, so the pair read as one new plan beside one long-standing one, when both
-- begin July 1, 2026. The name now says which regime it belongs to.


-- ---------------------------------------------------------------------------
-- 2026-08-17: real-dollar discounting (optional Advanced Analysis module)
-- ---------------------------------------------------------------------------
-- Adds two columns describing whether a row's earnings_premium and roi_pct were
-- computed in today's dollars with future money discounted, and at what rate.
--
-- RUN THIS BEFORE DEPLOYING. PostgREST rejects the ENTIRE row on an unknown
-- column (PGRST204), so until these exist every save from a session that
-- touched the module is silently lost -- and the survivors are biased toward
-- "discounting off", which is exactly the comparison the columns exist to make.

ALTER TABLE survey_responses ADD COLUMN IF NOT EXISTS discounting_enabled BOOLEAN;
ALTER TABLE survey_responses ADD COLUMN IF NOT EXISTS discount_rate_real   NUMERIC;
ALTER TABLE pdf_downloads    ADD COLUMN IF NOT EXISTS discounting_enabled BOOLEAN;
ALTER TABLE pdf_downloads    ADD COLUMN IF NOT EXISTS discount_rate_real   NUMERIC;
ALTER TABLE scenario_shares  ADD COLUMN IF NOT EXISTS discounting_enabled BOOLEAN;
ALTER TABLE scenario_shares  ADD COLUMN IF NOT EXISTS discount_rate_real   NUMERIC;
-- scenario_events too. It is easy to miss because CLAUDE.md's rule names three
-- tables, but maybe_log_scenario_event spreads the SAME build_scenario_context
-- dict, and the 2026-07-31 migration for hs_baseline_age_aware /
-- count_foregone_earnings correctly covered all four. It is also the highest
-- volume writer of the four, because it fires on RERUN rather than at a commit
-- point, so omitting it loses the most rows.
ALTER TABLE scenario_events  ADD COLUMN IF NOT EXISTS discounting_enabled BOOLEAN;
ALTER TABLE scenario_events  ADD COLUMN IF NOT EXISTS discount_rate_real   NUMERIC;

-- THIS IS NOT A SEAM IN THE EXISTING SERIES, and that is the whole reason the
-- module ships off by default. discounting_enabled false and NULL mean the same
-- thing (NULL is simply a row written before the column existed), so every row
-- ever recorded remains comparable with every other. Treat NULL as false, the
-- same rule hs_baseline_age_aware already carries.
--
-- What you MUST NOT do is pool discounted rows with undiscounted ones. The two
-- answer different questions: an undiscounted premium is "how many more dollars
-- pass through my hands", a discounted one is "what is that worth to me now".
-- Filter on discounting_enabled IS NOT TRUE for any series that spans this date.
--
-- Nor may discounted rows be pooled with EACH OTHER without conditioning on
-- discount_rate_real. The rate is a visitor-set input bounded 0 to 8%, and 1%
-- and 8% are not one treatment. The column is NULL whenever the flag is false,
-- so it never carries a rate that built nothing.
--
-- ON BACK-OUT: the module is designed to be removed (see DISCOUNTING_ENABLED in
-- app.py). If it is, these columns are RETAINED and simply stop being written,
-- exactly as the apprenticeship_* and 2026-plans columns were. Do not drop them:
-- the rows written while it was running are the only record of what those
-- visitors were shown, and a dropped column would leave their premium figures
-- unexplained rather than merely unusual. Record the back-out date here so the
-- window is recoverable from this file alone.


-- ---------------------------------------------------------------------------
-- 2026-08-18: the high-school baseline moved to real (today's-dollar) units
-- ---------------------------------------------------------------------------
-- HS_GRAD_GROWTH_RATE went from 0.02 to 0.0. This is a HARD SEAM in
-- earnings_premium and roi_pct across all five tables, and unlike the
-- discounting module it is NOT opt-in: every row from this date is computed a
-- different way, whatever the visitor did.

ALTER TABLE survey_responses ADD COLUMN IF NOT EXISTS hs_baseline_real_units BOOLEAN;
ALTER TABLE pdf_downloads    ADD COLUMN IF NOT EXISTS hs_baseline_real_units BOOLEAN;
ALTER TABLE scenario_shares  ADD COLUMN IF NOT EXISTS hs_baseline_real_units BOOLEAN;
-- And scenario_events, for the reason the discounting migration above records:
-- maybe_log_scenario_event spreads the same build_scenario_context dict, and it
-- fires on rerun, so it is the highest-volume writer of the four.
ALTER TABLE scenario_events  ADD COLUMN IF NOT EXISTS hs_baseline_real_units BOOLEAN;

-- WHAT CHANGED AND WHY IT MATTERS FOR ANALYSIS. The baseline used to grow 2% a
-- year on TOP of hs_age_factor, which by itself already supplies 2.17%/yr of
-- real progression from 18 to 40. The graduate side has no such term: its
-- growth is a cross-sectional OEWS p25-to-p50 gradient, median 2.14%/yr, with
-- no inflation in it at all. So the baseline compounded at roughly twice the
-- median career's rate, and every earnings_premium and roi_pct written before
-- this date is UNDERSTATED as a result.
--
-- Measured over all 836 occupations at the default 10-year window: median
-- premium +$38,450, and 130 occupations move from a negative premium to a
-- positive one. At 35 years: median +$755,137 and 294 occupations flip.
--
-- Treat NULL as false, the same rule hs_baseline_age_aware carries. DO NOT POOL
-- ACROSS THIS DATE for any premium or ROI figure. A count of sessions or
-- searches is unaffected; anything derived from the ROI model is not. The sign
-- flips are the sharpest reason: "did this major beat the baseline" is a
-- different question before and after, for 130 of 836 occupations at the
-- default horizon, so even a categorical pass/fail breakdown is not comparable.
--
-- This column is a constant True, like hs_baseline_age_aware. It is written
-- anyway because rows before the change carry NULL, and that is the only thing
-- in the data that separates the two eras.


-- ---------------------------------------------------------------------------
-- 2026-08-18: the career earnings curve plateaus instead of compounding
-- ---------------------------------------------------------------------------
ALTER TABLE survey_responses ADD COLUMN IF NOT EXISTS career_curve_plateaus BOOLEAN;
ALTER TABLE pdf_downloads    ADD COLUMN IF NOT EXISTS career_curve_plateaus BOOLEAN;
ALTER TABLE scenario_shares  ADD COLUMN IF NOT EXISTS career_curve_plateaus BOOLEAN;
ALTER TABLE scenario_events  ADD COLUMN IF NOT EXISTS career_curve_plateaus BOOLEAN;

-- All four tables, because build_scenario_context is spread into all four.
--
-- THIS SEAM IS NARROWER THAN THE OTHER TWO, and the difference matters for
-- analysis. get_major_growth_rate is fitted from OEWS p25 to p50, and beyond
-- year 10 the model used to keep compounding it, which put 595 of 825
-- occupations exactly at their own p90 by year 35. It now follows the CPS
-- graduate age profile after year 10 and plateaus; 21 remain at the ceiling.
--
-- Every salary through year 10 is BIT-IDENTICAL -- 9,196 points across all 836
-- occupations, verified against the pre-change file. So:
--
--   * rows with roi_horizon_years = 10 (the default, and the large majority)
--     are UNAFFECTED and pool freely across this date.
--   * rows with a longer horizon changed, and are not comparable across it.
--
-- Condition on career_curve_plateaus AND roi_horizon_years together. Filtering
-- on the flag alone needlessly discards every default-horizon row, which is
-- most of the table; ignoring it pools incomparable long-horizon figures.
-- Treat NULL as false, as with the other era flags.


-- ---------------------------------------------------------------------------
-- 2026-08-18 -- THE RESULTS PAGE WAS RESHAPED, no DDL.
--
-- No column changed and no row is invalid. This is a PRESENTATION seam, the
-- same kind as the 2026-08-12 wording one above and recorded for the same
-- reason: what a visitor was shown changed, while what was measured did not.
--
-- What changed, all in the single-scenario results branch:
--   * THE NET-POSITION CHART DRAWS 25 YEARS, down from 35. The earnings curve
--     began plateauing on this same date (career_curve_plateaus above), so the
--     last decade had become a straight line. Of 250 occupations at a $190,000
--     loan, 209 cross the baseline and only 9 of those cross after year 25, so
--     the shortened window hides a crossing for about 4% of paths -- which
--     still get it in words from crossover_phrase.
--   * THE NO-LOAN REFERENCE LINE IS SUPPRESSED WHEN IT CANNOT BE SEEN. Its
--     test was exact equality and is now a share of the drawn range, so a
--     small loan no longer draws a dashed twin directly on top of its solid
--     line. Where it is suppressed the caption states the borrowing cost in
--     dollars instead. Affects only sessions with that opt-in checkbox on.
--   * THE LOAN DETAIL IS COLLAPSED behind two expanders (the year-by-year
--     build-up table, and the balance and payment charts). Every WARNING stays
--     inline: the forgiveness note, the financing note, the Parent PLUS note
--     and the loan-basis disclosure.
--   * THE NET-POSITION BLOCK IS WRAPPED IN A TINTED CARD, so the checkbox, the
--     chart and its captions read as one panel. The figure itself was made
--     transparent to sit on it. Screen only: there is no card in the PDF, and
--     the matplotlib twin is deliberately unchanged, since the chart-twin rule
--     governs what a chart SHOWS rather than what colour its paper is.
--   * A CAPTION UNDER THE CHART NAMES WHERE EACH PATH PASSES THE BASELINE,
--     wording taken from crossover_phrase so it cannot disagree with the
--     verdict list that states the same moment. It exists for the case the
--     picture cannot show: the chart draws 25 years and net_position_crossover
--     searches 40, so a crossing at year 33 is off the right edge and the
--     caption says so instead of leaving the reader to hunt for two lines
--     meeting.
--
-- NO FIGURE MOVED as a result of any of this. The five changes are in
-- net_position_frame, NET_POSITION_CHART_YEARS, two st.expander calls, one
-- keyed st.container plus its CSS, and one caption built from an existing
-- shared helper; no constant, simulator or ROI path was touched, so every logged
-- earnings_premium, roi_pct, monthly_payment and payoff figure means on
-- 2026-08-19 exactly what it meant on 2026-08-17. Note that the SAME DATE
-- carries three real definitional seams (hs_baseline_real_units,
-- career_curve_plateaus, discounting_enabled) -- those are the ones that move
-- numbers, and they are recorded above with their own flags.
--
-- WHY THIS IS RECORDED AT ALL. The paper's outcomes are survey answers and
-- engagement, and both respond to what is visible without scrolling. Collapsing
-- two charts and a table moves the verdict chart most of a screen closer, so
-- PDF-download and share RATES either side of this date are not measuring the
-- same page. Condition on the date, or say in the write-up that layout was held
-- constant only within each side of it.
--
-- THE EXPERIMENT ARMS ARE UNAFFECTED RELATIVE TO EACH OTHER, but check this if
-- the layout is touched again: the expanders are single-branch-only, and that
-- is defensible ONLY because Compare Mode already draws its own comparison
-- charts here rather than these ones, so the two arms were never showing the
-- same pictures at this point in the page. render_payment_chart is still
-- called from both branches. A future collapse of something the two arms DO
-- share would be a genuine H2 confound.

-- ---------------------------------------------------------------------------
-- 2026-08-20: community-college cost moved to IPEDS, and gained a NON-RESIDENT
-- rate. NO DDL. cc_coa_a / cc_coa_b already exist and still hold the per-year
-- figure; what changed is where it comes from and that it now depends on a
-- residency answer the app never asked before.
--
-- TWO SEPARATE CHANGES IN ONE COMMIT, and only the second is a real seam.
--
-- 1. The in-district figures moved from a hand-typed NCES dict to
--    data/cc_costs_clean.csv, built from IPEDS by build_cc_costs.py. The two
--    sources agree closely -- median relative change across the 48 covered
--    states is 5% -- so this is a refinement rather than a break, and the
--    corroboration is the point: two independent federal sources landing
--    within a few percent is what makes either believable. California moved
--    $1,390 -> $1,288.
--
-- 2. A non-resident is now charged the OUT-OF-STATE rate. Before this every
--    visitor got the resident price whatever state they selected, so any
--    logged community-college scenario where the visitor was not a resident of
--    the selected state UNDERSTATES the cost -- by about 2x at the median and
--    7.7x in California. There is no flag on the old rows saying whether the
--    visitor was resident, because the app never asked, so those rows cannot
--    be corrected on read. Treat cc_coa_* before this date as in-district
--    regardless of the state, which is what it was.
--
-- The new share param is cc_res_a ("1"/"0"). It is NOT a column: residency
-- reaches the database only through the cc_coa_* figure it produces.
--
-- Four states are uncovered by IPEDS -- AK, DE, FL, NV -- because their
-- community colleges award bachelor's degrees and are filed as four-year
-- institutions. Those fall back to the national figure and the sidebar says so.


-- ===========================================================================
-- 2026-08-22  Every infographic gets its own ?src= tag. NO DDL; this is a
--             SEAM IN traffic_source, and the older side of it is not
--             recoverable.
-- ===========================================================================
--
-- Twelve chart scripts burned "worthmydegree.com/welcome?src=img" into their
-- pictures, so every click from any infographic arrived under one tag. The
-- charts are the most reposted surface this project has and the only question
-- anyone asks of them -- which picture actually brings people here -- was
-- unanswerable by construction.
--
-- Each chart now emits its own stem: community-college-careers-ca,
-- community-college-poster-ca, top-earning-careers, federal-cap, loan-true-cost,
-- transfer-path and the rest. The tag is DERIVED from the filename the script
-- writes rather than typed a twelfth time, because a wrong tag does not error --
-- it attributes the click to the wrong chart, which looks like an answer. That
-- nearly happened here: the first pass took the top-earning chart's stem from
-- its editorial variant and would have filed every click under
-- "top-earning-careers-editorial".
--
-- WHAT THIS COSTS. Rows with traffic_source = 'img' span every chart published
-- before this date and cannot be split: nothing in the row says which picture
-- it came from. Do not compare a per-chart count across this date, and do not
-- read the disappearance of 'img' as traffic falling.
--
-- Two tags predate it and keep their meaning: 'poster' was already
-- chart-specific and is now 'community-college-poster-ca', and 'reddit' is a
-- channel rather than a picture.
--
-- The 'img' rows stay. The anon key cannot UPDATE or DELETE, and rewriting them
-- would be inventing an attribution the data never had.


-- ===========================================================================
-- 2026-08-30  traffic_source takes only a tag-shaped value. NO DDL; a small
--             seam, and the older side of it is readable.
-- ===========================================================================
--
-- Both writers -- app.py's get_traffic_source and the edge Worker's srcTag --
-- now store ?src= only when it matches ^[A-Za-z0-9_-]{1,40}$, and NULL
-- otherwise. Every tag ever issued matches (marketing/README.md's taxonomy,
-- the per-chart filename stems, selftest, img, poster, reddit);
-- check_internal_links.py asserts that and that the two patterns are equal.
--
-- WHY. The column was unbounded text written from public routes for anyone
-- who cared to type a URL: a GET on a guide with a 20 KB ?src= stored the
-- 20 KB, on every row of every table the visit touched. It was the one value
-- in the row that came from the visitor with no shape check at all, where
-- action has had NAV_ORIGINS and knownSlug for a month.
--
-- WHAT THIS COSTS. Nothing recoverable is lost: a value that fails the rule
-- was never a channel, and NULL is what an untagged visit always was. Rows
-- before this date MAY hold a non-conforming string; treat those as untagged
-- when reading, which is what every row after this date already is.
-- 2026-08-30  Row level security. The anon key can INSERT and nothing else;
--             reads come through a `reporter` role that can SELECT and
--             nothing else; the edge counts likes through a function.
-- ===========================================================================
--
-- WHY. Until this date the repository held no RLS or policy SQL at all, and
-- the one privilege statement it did hold (the scenario_events grant above)
-- gave anon SELECT. A read-only probe on 2026-08-30 returned rows from all
-- five tables with the anon key, survey_responses.feedback_text included.
-- That key is in every edge Worker instance and in the Railway environment,
-- so the ?admin= gate protected the dashboard and not the data. Three
-- comments in this file said the anon key "cannot UPDATE or DELETE", which
-- is a claim about dashboard state no versioned SQL ever made true.
--
-- ORDER MATTERS, and each step is additive until the last:
--
--   0. The app must already send Prefer: return=minimal on every insert
--      (PR #176). postgrest's default makes every INSERT also a SELECT of the
--      written row, so an INSERT-only policy would fail every write silently
--      through the queue. The Worker already sends it. Confirm both hosts
--      have redeployed before pasting anything below.
--
--   1. The count function the Worker calls instead of reading usage_logs.

create or replace function reaction_count(p_action text)
returns bigint
language sql stable security definer
set search_path = public
as $$ select count(*) from usage_logs where action = p_action $$;
revoke all on function reaction_count(text) from public;
grant execute on function reaction_count(text) to anon;

--   2. The reporter role. NOLOGIN: it is reached only through a JWT whose
--      role claim names it, which PostgREST honours because authenticator
--      may SET ROLE to it. Mint the JWT with infra/mint_reporter_jwt.py and
--      store it as SUPABASE_READ_KEY; then deploy the code that reads with
--      it (the admin dashboard, analyze_survey.py, analyze_traffic.py,
--      infra/rank_charts.py) and the Worker that calls reaction_count.

create role reporter nologin;
grant reporter to authenticator;
grant usage on schema public to reporter;
grant select on all tables in schema public to reporter;
alter default privileges in schema public grant select on tables to reporter;

--   3. ONLY AFTER 2 IS LIVE: enable RLS, allow anon to insert, allow reporter
--      to select, and take SELECT away from anon. Per table, all five.

do $$
declare t text;
begin
  foreach t in array array['usage_logs', 'scenario_events', 'pdf_downloads',
                           'scenario_shares', 'survey_responses']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy anon_insert on %I for insert to anon with check (true)', t);
    execute format('create policy reporter_select on %I for select to reporter using (true)', t);
    execute format('revoke select on %I from anon', t);
  end loop;
end $$;

--   4. Verify: with the anon key, GET /rest/v1/<table>?select=*&limit=1
--      returns 200 [] on every table; the Helpful count still renders; the
--      admin dashboard populates; analyze_traffic.py runs.
--
--   Rollback, per table: alter table T disable row level security;
--
-- NOT A SEAM in the data: no row changes meaning. What changes is who can
-- read them, which is the point.

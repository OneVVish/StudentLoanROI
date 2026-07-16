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

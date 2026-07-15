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

-- Repair existing archives whose original category constraint predates IV study
-- collection. One atomic ALTER retains every original category and all rows.
-- RLS, grants, record hashes and MONITOR_ONLY remain unchanged.
set local lock_timeout = '5s';
alter table public.trading_research_records
  drop constraint trading_research_records_category_check,
  add constraint trading_research_records_category_check check (category in (
    'impulse_ticks','impulse_event','impulse_outcome','daily_summary',
    'algo_order','algo_state','algo_pnl','iv30_research_day'
  ));

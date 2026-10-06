-- Additive archive only. Existing broker/strategy/Qwen tables are untouched.
create table public.trading_research_records (
  source_id uuid not null,
  record_key text not null,
  category text not null check (category in ('impulse_ticks','impulse_event','impulse_outcome','daily_summary','algo_order','algo_state','algo_pnl')),
  trade_date date not null,
  index_name text check (index_name in ('NIFTY','SENSEX') or index_name is null),
  observed_at timestamptz not null,
  body jsonb not null,
  content_sha256 text not null check (content_sha256 ~ '^[a-f0-9]{64}$'),
  mode text not null default 'MONITOR_ONLY' check (mode = 'MONITOR_ONLY'),
  uploaded_at timestamptz not null default now(),
  primary key (source_id,record_key)
);
create index trading_research_day_category on public.trading_research_records(trade_date,category,index_name);
alter table public.trading_research_records enable row level security;
revoke all on public.trading_research_records from public,anon,authenticated;
grant select,insert,update on public.trading_research_records to service_role;
comment on table public.trading_research_records is 'Private Oracle research/algo archive. Not a command, order or activation interface. Tick chunks are lossless gzip+base64 JSON.';

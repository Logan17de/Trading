-- Cloud-only VM outage source. This does NOT enable or change broker trading.
-- No credentials, symbols, user identity, price or order details stored.
create table if not exists public.trading_safety_state (
  source text primary key check (source = 'oracle'),
  risk_state text not null check (risk_state in ('ACTIVE','FLAT','UNKNOWN')),
  observed_at timestamptz not null,
  updated_at timestamptz not null default clock_timestamp()
);

create or replace function public.trading_safety_updated()
returns trigger language plpgsql set search_path = '' as $$
begin
  new.updated_at := clock_timestamp();
  return new;
end;
$$;
drop trigger if exists trading_safety_updated on public.trading_safety_state;
create trigger trading_safety_updated before update on public.trading_safety_state
for each row execute function public.trading_safety_updated();

create table if not exists public.trading_vm_safety_outbox (
  singleton boolean primary key default true check (singleton),
  outage_detected_at timestamptz,
  next_due_at timestamptz not null default '-infinity',
  pending_token uuid,
  pending_since timestamptz,
  last_accepted_at timestamptz,
  risk_state text not null default 'UNKNOWN'
    check (risk_state in ('ACTIVE','FLAT','UNKNOWN'))
);
insert into public.trading_vm_safety_outbox(singleton)
values (true) on conflict (singleton) do nothing;

alter table public.trading_safety_state enable row level security;
alter table public.trading_vm_safety_outbox enable row level security;
revoke all on public.trading_safety_state from public, anon, authenticated;
revoke all on public.trading_vm_safety_outbox from public, anon, authenticated;
grant select, insert, update on public.trading_safety_state to service_role;
grant select on public.trading_vm_safety_outbox to service_role;

-- Atomic, durable claim for at-most-one pending email idempotency key.
-- POST /rest/v1/rpc/claim_trading_vm_outage with the service role only.
create or replace function public.claim_trading_vm_outage()
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  beat public.trading_safety_state%rowtype;
  row public.trading_vm_safety_outbox%rowtype;
  at timestamptz := clock_timestamp();
  local_at timestamp;
  quiet boolean;
  stale boolean;
  token uuid;
begin
  select * into beat from public.trading_safety_state where source='oracle';
  if not found then
    return jsonb_build_object('status','HEARTBEAT_NOT_CONFIGURED');
  end if;

  select * into row from public.trading_vm_safety_outbox
    where singleton=true for update;
  stale := beat.updated_at < at - interval '120 seconds'
           or beat.updated_at > at + interval '15 seconds';
  if not stale then
    -- Mark recovery only after fresh, trusted server-side heartbeat.
    if row.outage_detected_at is not null then
      update public.trading_vm_safety_outbox
      set outage_detected_at=null,pending_token=null,pending_since=null,
          next_due_at='-infinity',risk_state=beat.risk_state
      where singleton=true;
    end if;
    return jsonb_build_object('status','HEALTHY');
  end if;

  local_at := at at time zone 'Asia/Tokyo';
  -- Idle reminders run in the existing, reviewed 2026 session window only.
  -- Active or unknown exposure is urgent across nights, weekends and holidays.
  quiet := beat.risk_state='FLAT' and (
    extract(isodow from local_at) > 5
    or extract(year from local_at) <> 2026
    or local_at::time < time '12:45'
    or local_at::time >= time '19:00'
    or local_at::date in (date '2026-10-20',date '2026-11-10',
                          date '2026-11-24',date '2026-12-25')
  );
  if row.outage_detected_at is null then
    update public.trading_vm_safety_outbox
    set outage_detected_at=at,risk_state=beat.risk_state,
        pending_token=null,pending_since=null,next_due_at='-infinity'
    where singleton=true;
    row.outage_detected_at:=at;
    row.pending_token:=null;
    row.next_due_at:='-infinity';
  end if;
  if quiet then
    return jsonb_build_object('status','IDLE_QUIET_WINDOW');
  end if;
  if at < row.next_due_at then
    return jsonb_build_object('status','NOT_DUE');
  end if;
  if row.pending_token is null or row.pending_since < at - interval '23 hours' then
    token:=gen_random_uuid();
    update public.trading_vm_safety_outbox
    set pending_token=token,pending_since=at,risk_state=beat.risk_state,
        next_due_at=at+interval '5 minutes'
    where singleton=true;
  else
    token:=row.pending_token;
    update public.trading_vm_safety_outbox
    set next_due_at=at+interval '5 minutes',risk_state=beat.risk_state
    where singleton=true;
  end if;
  return jsonb_build_object('status','SEND','token',token,
       'risk_state',beat.risk_state,
       'outage_detected_at',row.outage_detected_at);
end;
$$;

create or replace function public.ack_trading_vm_outage(token uuid)
returns jsonb
language plpgsql security definer set search_path = ''
as $$
declare
  updated_count integer;
begin
  update public.trading_vm_safety_outbox
  set pending_token=null,pending_since=null,last_accepted_at=clock_timestamp(),
      next_due_at=clock_timestamp()+
         case when risk_state='FLAT' then interval '2 hours'
              else interval '20 minutes' end
  where singleton and pending_token=token;
  get diagnostics updated_count=row_count;
  return jsonb_build_object('acknowledged',updated_count=1);
end;
$$;

revoke all on function public.trading_safety_updated() from public,anon,authenticated;
revoke all on function public.claim_trading_vm_outage() from public,anon,authenticated;
revoke all on function public.ack_trading_vm_outage(uuid) from public,anon,authenticated;
grant execute on function public.claim_trading_vm_outage() to service_role;
grant execute on function public.ack_trading_vm_outage(uuid) to service_role;

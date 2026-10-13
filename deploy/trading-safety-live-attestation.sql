alter table public.trading_vm_safety_outbox
add column if not exists last_checked_at timestamptz;

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
  update public.trading_vm_safety_outbox
  set last_checked_at=clock_timestamp()
  where singleton=true;
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



create or replace function public.trading_vm_cron_ready()
returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1
    from cron.job j
    join lateral (
      select d.status,d.end_time
      from cron.job_run_details d
      where d.jobid=j.jobid
      order by d.start_time desc limit 1
    ) latest on true
    where j.jobname='trading-vm-safety-every-minute'
      and j.schedule='* * * * *'
      and j.active is true
      and latest.status='succeeded'
      and latest.end_time >= clock_timestamp()-interval '180 seconds'
      and exists (
        select 1 from public.trading_vm_safety_outbox s
        where s.singleton and s.last_checked_at >= clock_timestamp()-interval '180 seconds'
      )
  );
$$;
revoke all on function public.trading_vm_cron_ready() from public,anon,authenticated;
grant execute on function public.trading_vm_cron_ready() to service_role;

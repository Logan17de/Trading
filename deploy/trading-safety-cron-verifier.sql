-- Private guard: a live entry must not assume that an unconfigured cloud cron works.
-- This function discloses only a boolean, and only to the service_role.
create or replace function public.trading_vm_cron_ready()
returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1
    from cron.job j
    where j.jobname='trading-vm-safety-every-minute'
      and j.schedule='* * * * *'
      and j.active is true
  );
$$;
revoke all on function public.trading_vm_cron_ready() from public,anon,authenticated;
grant execute on function public.trading_vm_cron_ready() to service_role;

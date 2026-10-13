# Guarded LIVE Algo Start: release and operator checklist

**Status of this code:** implementation and synthetic tests only. No live broker
orders, OCI restart or pause removal were executed by this change. No auto-start
is permitted from a mere button press if required real-world evidence is absent.

## User experience

- **Algo Off** means no new entries or engine broker writes.
- After genuine *one-time* protected commissioning, verified cloud failure
  emails, selection of the explicit executable strategy, and owner
  preparation of live mode, click **Algo Start** once.
- The Oracle backend reevaluates immutable release/account identity, live mode,
  pause, original provider activation receipt, fresh complete broker snapshot,
  selected strategy and alert commissioning. If any check fails, it returns
  **START_REFUSED**, leaving Algo Off and showing exact blockers. It never
  stores an On request that might silently activate later.
- If all checks pass, **Algo On** is saved. The engine starts eligible entries
  only when the 14:00–19:00 JST entry window, current funds, hedge-first fills,
  market conditions, stop protection and the other gates all pass. No
  forced/immediate market order and no guaranteed daily trade.
- On every future **new entry/roll**, a fresh Oracle-cloud heartbeat (≤90 s) and
  owner-verified alert evidence (≤7 days) are rechecked. **Existing owned exits
  and protection do not depend on that new-entry alert gate.** However, global
  Algo Off, paper mode and runtime pause already deny broker writes, including
  exits, under the existing execution policy. Broker-held stops may still act.
- Only `normal_theta` is executable currently. It is an **unbacktested** hedge
  spread hypothesis, not the original call-only selling algorithm and not
  guaranteed profitable. Both Normal and Research default Off. Do not
  auto-enable Normal or promote Research to execution by pressing Start.

## Already completed in Growing-Trader Supabase

- Protected tables `trading_safety_state`, `trading_vm_safety_outbox`
  with RLS and service-role RPC grants.
- Authenticated `trading-vm-safety` Edge Function deployed, no valid LIVE
  notification sent yet.
- `pg_cron` and `pg_net` extensions enabled.
- Private function `trading_vm_cron_ready()` now checks the exact job name,
  one-minute frequency, recent successful dispatch and a recent Edge
  invocation. It currently returns **false** because the job is not installed.
- Prior synthetic SQL outage test was rolled back; no fake live heartbeat remains.

## Required protected configuration

1. In the Supabase project's **Edge Functions → Secrets**, configure
   `TRADING_SAFETY_RESEND_KEY`, `TRADING_SAFETY_FROM`,
   `TRADING_SAFETY_TO`. Use a verified domain sender and approved recipient.
   Keep secrets in the platform, never in GitHub or chat.
2. In **Supabase Vault**, create a secret named
   `trading_safety_scheduler_jwt` containing a valid **legacy JWT** service
   role credential for the current `verify_jwt=true` Edge gateway. Do not
   use a non-JWT `sb_secret_...` key in an Authorization Bearer header; the
   gateway will reject it.
3. Schedule **only one** authenticated, one-minute cron job with the exact
   name `trading-vm-safety-every-minute`. Execute the reviewed SQL below in
   Supabase SQL Editor **after** the Vault secret is present.

```sql
select cron.schedule(
  'trading-vm-safety-every-minute',
  '* * * * *',
  $job$
  select net.http_post(
    url := 'https://imirspxhbnerxknyynqx.supabase.co/functions/v1/trading-vm-safety',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization',
        'Bearer ' || (select decrypted_secret
                       from vault.decrypted_secrets
                       where name='trading_safety_scheduler_jwt')
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 8000
  );
  $job$
);
```

4. Verify the Edge Function logs show authenticated checks, and
   `trading_vm_cron_ready()` reports true only after a recent successful
   one-minute cron run and a recent Edge invocation. **This does not prove
   email delivery**. The independent cloud outbox must separately record a
   real provider-accepted outage alert before safety certification can pass.
5. Deploy the immutable reviewed release on Oracle through the existing
   owner-controlled rollout. Verify broker and local journal backups, original
   Oracle release/pause/mode, account identity and unchanged shared VM
   services. **Do not arm live or restart an active, unresolved executor.**
   The new Oracle service publishes a privacy-preserving cloud heartbeat
   every 30 seconds. Keep any other project on the same VM untouched.
6. Test email from the independent Oracle monitor **explicitly**:
   `python -I -m nifty_engine.agent_engine.failure_alerts test-email`,
   with the existing root-protected environment loaded by the approved
   service. Then run `receipt` and verify the provider's exact envelope
   and recipient-server delivered/opened acknowledgment.
7. **While confirmed flat**, test an actual cloud VM-heartbeat outage and
   restoration without disabling a production active trader, with a verified
   owner email from the external watcher. Never stop an active VM merely to
   prove this alarm. Ensure provider acceptance and successful recovery.
8. As root on the immutable Oracle release, run:
   `python -I -m nifty_engine.agent_engine.safety_certify check`.
   Once it reports `EVIDENCE_READY_FOR_EXPLICIT_OWNER_INSTALL`, explicitly
   run `python -I -m nifty_engine.agent_engine.safety_certify install
   --confirm I_VERIFIED_SAFETY_TESTS`. This verifies actual local and cloud
   receipts, runs synthetic safety tests without credentials, drops privileges
   to `trading-observer`, and installs a release-bound private alert
   attestation. It **does not** activate LIVE or place orders.
9. **Separately** complete the existing owner-controlled Groww
   `Real trading setup`: explicit BUY + protect, full fill, real stop
   persistence and generated child linkage, flat-after-exit reconciliation,
   exact provider/release/policy binding, and owner-approved Prepare Live.
   Tests must be supervised and may use real money. An unfilled/rejected
   order is not a successful commissioning test.
10. When the dashboard shows the verified live setup, enable the desired
   approved executable strategy, then press **Algo Start**. It will refuse
   until the gates are satisfied. Once saved On, ordinary autonomous
   entry/exit handling follows the preexisting strategy rules.

## Deployment and evidence limitations

No external tool in this conversation could SSH to the current Oracle VM or
read the owner's root-protected mail vault and Groww credentials. Therefore the
actual email configuration, cloud schedule, first Oracle heartbeat, private
safety attestation, provider stop-child commissioning and LIVE arm are **not**
claimed as completed. The default remains safe/paper/paused/Off.

A GTT can trigger a limit order that never fills; a non-atomic cancel-stop and
target-exit sequence can briefly leave a position unprotected. An independent
cloud alarm is not a substitute for a durable broker-side stop and cannot
guarantee immediate manual intervention or cap loss. All risk remains the
account owner's.

[Owner broker commissioning](LIVE_SETUP.md) |
[Exclusive safety-mail policy](SAFETY_ALERT_POLICY.md) |
[Oracle execution protocol](ORACLE_EXECUTION.md)

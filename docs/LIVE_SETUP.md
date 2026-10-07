# Owner setup for real trading

The normal executor and desktop Start control are installed. One-time broker
commissioning verifies the actual provider behavior before preparing live mode.
These actions are for the owner to run. Deployment and background workers never
invoke them. Keep Algo Off throughout setup. No commissioning trade has been run
by maintenance, and a successful synthetic test is not provider evidence.

The test creates one explicitly chosen long option through a GTT BUY, then an
explicit LIMIT SELL of that test's acquired quantity. Purchase debit is limited
to INR 1,000 before fees. It does not sell a naked option, choose an instrument,
change the owner's prices to force a fill, or use a pre-existing manual position.
Only NIFTY/SENSEX FNO NRML, one current-master lot and a non-expiry-day contract
are accepted. Keep the purchase amount and charges available. A test may remain
pending if its trigger is not reached or its limit does not fill.

## Use the desktop app

Open **Real trading setup** from Options Trader, or visit
`http://127.0.0.1:8765/live-setup` in Edge. No command prompt is required.

1. Enter the exact current contract, one-lot quantity, tick size, your trigger and
   limit prices, plan deadline and existing Oracle IPv4 approved in Groww.
2. Click **Preview · no order**, then review the exact values displayed.
3. Check the owner confirmation and click **Submit real BUY GTT** only when ready.
4. Use **Read broker evidence** for the actual parent and fill observations. Each
   observation is a separate request; the two persistence/flat reads must be at
   least five seconds apart. The page never submits or retries orders by polling.
5. When the test long is filled, confirm **Close test long**. If an instruction is
   pending, use its exact cancel action, then read evidence again.
6. After the test is COMPLETE, click **Check live readiness**, review its result,
   then confirm **Prepare live · keep Algo Off**. This may take several minutes.

The PC saves the pending plan before sending it and resumes it after restart.
A lost response stays uncertain until the original plan is read back. Do not
create another test to work around an unknown result. Test actions use the fixed
protected SSH connection, never browser-held broker credentials or a public port.
The following command-line workflow is an alternative for the owner.

## Private plan and preview

Create a JSON file under `D:\Money Trader\Trading\.agent-state`, using actual
current-master values and your chosen prices. Never include credentials. The
required fields are:

| Field | Value to supply |
| --- | --- |
| `contract.symbol` | Exact Groww trading symbol |
| `contract.index` | `NIFTY` or `SENSEX` |
| `contract.expiry` | Actual `YYYY-MM-DD` expiry, after today |
| `contract.lot_size` | Current instrument-master lot size |
| `contract.tick_size` | Current instrument-master tick size |
| `quantity` | Exactly one lot |
| `trigger_price` | Your BUY UP trigger, above the current ask |
| `buy_limit_price` | At/above trigger, on tick; quantity times price <=1,000 |
| `sell_limit_price` | Your explicit test closing LIMIT price, on tick |
| `valid_until` | Zoned ISO timestamp within the next 24 hours |
| `egress_ip` | Oracle's existing broker-whitelisted public IPv4 |

From the Trading folder:

```powershell
.\scripts\Invoke-OracleLiveSetup.ps1 -Action preview -SpecFile .\.agent-state\broker-test.json
```

Preview authenticates from Oracle, checks the account, current master, recent
quote, available buying balance, positions, ordinary orders and active smart
orders. It compares the declared IP with observed Oracle egress; a successful
preview does not prove that Groww accepts order writes. It sends no orders.
It returns a private `plan_id` and `plan_hash` for the exact normalized plan.
Save them privately. The plan remains bound to the release, policy, SDK, account
and observed IP. Future commands cannot silently change that binding.

## Run and observe the exact owner test

Replace the placeholders with the preview's actual values. `submit`, `close`
and `cancel` are real broker actions and require the exact plan hash every time.

```powershell
.\scripts\Invoke-OracleLiveSetup.ps1 -Action submit -PlanId PLAN_ID -Confirm PLAN_HASH
.\scripts\Invoke-OracleLiveSetup.ps1 -Action capture -PlanId PLAN_ID
.\scripts\Invoke-OracleLiveSetup.ps1 -Action status -PlanId PLAN_ID
```

The test must be observed ACTIVE by separate command processes at least five
seconds apart, with broker validity beyond contract expiry. After an actual
trigger it must return the original GTT reference on the exact generated order.
The verifier checks the contract, quantity, direction, price, terminal status and
full fill. If linkage is absent it reports unverified; it never guesses from net
positions or adopts a manual order. A timeout never causes a repeated submission.

Once the exact acquired long has a terminal fill, close it at your predeclared
sell limit and capture two fresh flat reads at least five seconds apart:

```powershell
.\scripts\Invoke-OracleLiveSetup.ps1 -Action close -PlanId PLAN_ID -Confirm PLAN_HASH
.\scripts\Invoke-OracleLiveSetup.ps1 -Action capture -PlanId PLAN_ID
```

Repeat the read-only `capture` command after five seconds. A pending or partial
order cannot become a completed test. If the GTT is still ACTIVE and untriggered,
the owner can cancel that exact parent with `-Action cancel` and the plan hash.
Cancellation alone does not verify generated-child execution. Do not create a new
plan to recover an uncertain order. Inspect its original status; external changes
to the test symbol stop automated test cleanup and require owner reconciliation.

## Prepare live mode, leaving Algo Off

Only a COMPLETE test with actual parent, child, API close, terminal orders and
two confirmed flat reads produces verifiable evidence. The app shows these facts
without exposing account IDs or private order details.

```powershell
.\scripts\Invoke-OracleLiveSetup.ps1 -Action arm-preview -PlanId PLAN_ID
.\scripts\Invoke-OracleLiveSetup.ps1 -Action arm -PlanId PLAN_ID -Confirm EVIDENCE_DIGEST
```

`arm-preview` returns the exact evidence digest without changing trading mode.
`arm` revalidates the account and installed release, runs the deployed replay
tests without broker credentials, checks Algo Off/no owned basket, backs up the
journal and installs the evidence-backed receipt. It then prepares live mode and
clears only this runtime's pause link. The original shared trader pause is kept.
Only the observer service restarts; shared Qwen/mail/tunnels are untouched.
Failed setup restores paper/pause before restarting. Global Algo remains Off.

Now select **Normal theta spread** and click **Algo Start** in Options Trader.
After 14:00 JST, a fresh qualifying trend, manual-position exclusion, margin,
books/Greeks, current expiry, one basket and risk checks must pass. The executor
verifies the bought hedge before the short and reads back persistent protection.
It waits when there is no qualifying spread. Research cannot submit new trades.

Algo Off stops future engine writes. Existing exchange/GTT instructions may still
trigger or fill. Exact release or policy changes invalidate setup; deployment
refuses an owned open basket or owner On and preserves operational mode/pause.
It does not silently transfer verification to a new build. Repeat reviewed setup
for a changed build before expecting Start to execute again.

## What is and is not proved

Groww documents API GTT parents as lasting one year by default. The request's
`duration=DAY` is the generated order's validity after a trigger, not the parent
GTT's lifetime. The verifier therefore checks the parent's actual `expire_at`
and persistence separately from the generated order and its fill. A historical
manual target shown on the website cannot supply those API-specific receipts.

Completed commissioning proves the recorded requests/readbacks worked for the
exact account, IP, release and test. It does not establish profitability or assure
future fills. A stop trigger is not a guaranteed realized-loss cap. All test
observations live in a separate private journal namespace and are never relabeled
as algo strategy performance. Manual trades keep their existing ownership.

Provider contracts: [Groww smart orders](https://groww.in/trade-api/docs/curl/smart-orders),
[Groww orders](https://groww.in/trade-api/docs/curl/orders), and
[Groww static IP setup](https://groww.in/blog/static-ip-api-trading-setup).

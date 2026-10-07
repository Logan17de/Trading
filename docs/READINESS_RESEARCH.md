# Readiness evidence and the 38-day study

October 7: the owner chose a 38-calendar-day research period for strategies
requiring further evidence. The three credit basket routes are implemented;
that is distinct from live readiness. Calendar remains monitor-only. No strategy
is activated by beginning or finishing this study.

## Connected evidence

- The existing minute research worker fetches official RBI MPC and FOMC schedules.
  Their complete annual meeting sections must parse. MoSPI's public latest-document
  API must identify the exact reviewed PDF, with a matching SHA256. An unavailable
  source or changed/unreviewed PDF stays UNKNOWN. No news worker is restarted.
- Scheduled coverage is RBI MPC, FOMC and Indian CPI/IIP/GDP, through the existing
  prospective holding horizon. Source-local whole days are retained because exact
  announcement times are not supplied. MoSPI holiday shifts are explicitly not
  guessed. This is scheduled-event coverage, not proof that unscheduled risks
  cannot occur. Original source check times expire after one hour.
- The existing entry policy still requires an event-free 38-day holding horizon.
  Monthly CPI/IIP and policy meetings therefore normally produce EVENT_RISK.
  The owner selected research instead of a proposed 24-hour blackout change.
  This restrictive policy is retained; a complete calendar is not a clear calendar.
- Both indexes collect a versioned ATM IV30 research proxy from actual Groww
  chains at listed expiries bracketing 30 days and independently observed spot.
  Average CE/PE variance at the nearest common listed strike is interpolated
  in total variance across terms. No extrapolation or synthetic expiry is used.
  Both receipts must be current and within ten seconds of each other.
- This proxy is NOT India VIX, a model-free volatility index, individual-contract
  IV, or an execution feature. It is never substituted into strategy inputs.
  Actual 15:25–15:30 IST receipts are retained privately by index/day for a
  future matched series. Intraday points, missing sessions and holidays are
  not filled forward. Counts mean observed days, not a certified session calendar.
  The original matched 252-prior-session IV requirement is unchanged.
- A bounded GET-only GTT diagnostic checks ACTIVE/COMPLETED/CANCELLED list pages.
  Current counts apply to an explicit28-day IST date range. Earlier October7
  premarket checks used the default range, so their zero counts were today-only.
  An empty successful response
  proves read access only. It does not certify parent persistence, generated-child
  linkage, fills, cancellation races or order-write access.

Official sources:
[RBI schedule](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=62422),
[FOMC calendars](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm),
[MoSPI calendar](https://www.mospi.gov.in/release-calendar).
Reviewed MoSPI document: September 1 publication, updated through August 2026;
SHA256 `33156099f6e9c4767d71f948112a23c5ed56b59af8a24c49e8c67ab419ed8172`.
Dates for October 2026–March 2027 were checked on the actual PDF pages 5–8.

## Private campaign

`report-research-campaign-v1` records the actual start, an end 38 calendar days
later, both indexes, all four report hypotheses, and actual observed market days.
Starting it again preserves the original period. The existing worker records
actual fresh Greek/book availability and calendar state; monitoring remains
independent of owner strategy switches. At the deadline, status becomes
COLLECTION_ENDED_REVIEW_REQUIRED. There is no automatic deployment, activation,
new strategy selection, profitability claim or shortening of data requirements.

Private IV day observations, calendar snapshots, campaign checkpoints and provider
read diagnostics use the existing one-way Supabase outbox. Report-owned order
attribution now joins that archive. Manual positions, account capital and secrets
are excluded. Source receipts, unknowns and methodology survive restart.

## Remaining production validation

1. Obtain or validate a matching dated 252-session Indian IV benchmark for both
   indexes; preserve methodology, units, independent observation dates and actual
   exchange-session coverage. Thirty-eight calendar days cannot create that history.
   MATCHED_IV_IMPORT.md documents the installed private evidence-import path;
   it does not claim a supplied dataset or configured provider stream.
2. Review whether the strict event-free holding-horizon policy is useful after
   collecting evidence. Changing the blackout policy requires the owner's decision.
3. Verify actual broker order acknowledgment and persistent FNO NRML GTT readback
   on a deliberately reviewed owner-controlled validation flow. Receipt acceptance
   alone is not persistence or a fill. Check validity beyond contract expiry.
4. Verify that a triggered parent yields the exact original reserved reference
   in the generated child, with matching ID, symbol, exchange, quantity, side,
   product, order type, limit and validity. Groww's published schema does not
   promise this linkage. If different, implement and test a provider-proven link;
   never match only by contract/quantity or adopt a manual order.
5. Check modification and cancel/trigger races with reconciled terminal child
   evidence before approving any replacement close. Unknown results freeze writes.
6. Complete calendar's multi-expiry settlement/payoff and margin risk model before
   considering an execution route; debit is not asserted to bound economic loss.

Maintenance cannot supply the real-order evidence in steps 3–5 by placing orders,
creating all-true activation flags, removing pause or changing LIVE. The current
private activation record must stay absent until real evidence is reviewed for
the exact immutable release and policy. Start and strategy switches do not bypass
that requirement. The observer and shared Oracle services remain running.

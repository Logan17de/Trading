# Normal theta spread and Research

October 7 owner requested a normal selling strategy derived from the report,
separate from the historical-IV research. The dashboard has two private choices:
**Normal theta spread** and **Research**. Both default Off; no migration enables
either or changes global Algo intent. Research never grants order permission.
The original four preferences and exact position ownership remain provenance.

## Normal policy

This is a new, unbacktested hypothesis for both NIFTY and SENSEX, evaluated when
fresh evidence is available. It does not guarantee a daily trade or profit.
There is no former weekday routing, fixed ₹20/₹80 target, expiry entry or rollover.

| Check | Rule |
| --- | --- |
| Direction | Fresh spot above prior MA20 above MA50, ADX14 ≥25: bull put. Below both in reverse order: bear call. Otherwise NO_TRADE. |
| Short | Out-of-the-money option with signed absolute delta .15–.25 |
| Hedge | Equal quantity, same actual expiry; lower put or higher call. Buy first and verify its fill before selling. |
| Expiry | API/current-master intersection, 14–45 calendar days remaining; no weekday assumption |
| Theta | Both long-option model theta values negative; bought theta minus sold theta positive. Same Groww provider convention. |
| Quotes | Positive two-sided quotes, sufficient displayed quantity, spread ≤10% of midpoint; dated Greeks and books required |
| Size | One owned basket; compare one/two lots using current actual balances and exact hypothetical broker margin/charges |
| Risk | Quoted expiry loss after estimated round-trip charges ≤₹1,000; hedge-only debit plus costs also ≤₹1,000 |
| Selection | Highest net expiry reward/risk among sampled valid baskets, then lower margin; not a global optimum or probability estimate |
| Entry | Weekdays 14:00 inclusive–19:00 exclusive JST, confirmed broker session and all execution gates |
| Exit | Half of actual net entry credit after estimated costs; loss trigger is the smaller of ₹1,000 and 1.5 times gross entry credit; close at seven or fewer calendar DTE |

Held exact-owned baskets are reviewed outside the fresh-entry window while the
exchange is open. Disabling their strategy stops new entries/incomplete entry,
but retains completed-basket protection/exits while global Algo remains On.
Global Off/pause/paper deny all engine writes, including exits. Already armed
broker instructions can still act independently. No manual trade is adopted.

Stop triggers, limits and a theoretical expiry payoff bound do not guarantee a
maximum realized loss during gaps, leg execution or fees. Net positive theta
does not neutralize direction, gamma or volatility. The Groww API documentation
defines theta as time decay but does not specify its time/unit convention; code
uses only the relative sign and stores `groww_native`, not an INR/day forecast.

The 14–45 DTE, trend, quote-width and exit parameters are explicitly declared
implementation hypotheses, not empirically optimized recommendations. A ₹1,000
bound can exclude all listed spreads. Missing or unaffordable data stays WAIT /
NO_TRADE rather than relaxing risk or selling without a hedge.

The normal policy does **not** use the 252-session IV percentile, volatility risk
premium or strict 38-day event-free horizon. The official scheduled-event status
remains attached and visible as risk information; it is not an entry gate for
this separate hypothesis. Research's original calendar/IV policy is unchanged.
The owner's retired news collectors/gates stay retired.

## Research and integration

Bull put, bear call, iron condor and calendar remain four monitor-only studies
inside the Research group. The original private 38-calendar-day campaign keeps
collecting even when the group is deselected; selecting Research neither resets
the study nor promotes it to execution. Its 252-session historical-IV requirement
is unchanged. Research selection is a saved owner preference, not permission to
place research orders. Existing report-owned baskets retain original management.

Normal uses the existing PremiumExecutor / hedged-basket lifecycle, journal,
exact-reference order gateway, persistent protection and manual-trade exclusion.
No new runtime or public trading endpoint. A separate evidence cache prevents
normal samples from replacing report studies. Hypothetical margin calls do not
place orders. The archive allowlist includes normal evaluations/contracts and
group preferences; accounting values and secrets remain private and out of Git.

Live activation still requires actual reviewed provider-write, persistent GTT and
generated-child linkage evidence for the immutable release/policy. Synthetic
replays cannot provide it. Start saves intent; it cannot bypass these gates.
Maintenance leaves Algo Off, paper mode and `.trader-paused` intact.

## Primary references

- [OIC bear call spread](https://prd-web.optionseducation.org/strategies/all-strategies/bear-call-spread-credit-call-spread): same-expiry short call plus higher long call; bounded expiry reward/risk; time decay can be offset by the long hedge.
- [OIC bull put spread](https://prd-web.optionseducation.org/strategies/all-strategies/bull-put-spread-credit-put-spread): short put plus lower long put; bullish limited-risk credit structure.
- [Groww live data](https://groww.in/trade-api/docs/curl/live-data): option-chain signed Greeks and independent market quotes.

OIC examples describe US equity options. Their assignment rules and lot sizes
are not transplanted into Indian index contracts; current Groww/master metadata
and actual expiry are required. Fictional SPX report performance is not used.

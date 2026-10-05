"""Offline comparison of specified hedged calls. No pricing/order API or execution."""
from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path

from .contracts import dumps, integer, keys
from .market_check import safe_error


def positive(value):
    if type(value) not in (float,int) or not math.isfinite(value) or value <= 0:
        raise ValueError('positive finite number required')
    return value


def compare(value):
    keys(value, {'instrument','exchange','expiry','short_strike','short_bid','hedges',
                 'lot_size','quantity','round_trip_cost_inr'}, {'margin_budget_inr'})
    if (value['instrument'],value['exchange']) not in (('NIFTY','NSE'),('BANKNIFTY','NSE'),('SENSEX','BSE')):
        raise ValueError('unsupported index/exchange')
    date.fromisoformat(value['expiry'])
    short = positive(value['short_strike'])
    bid = positive(value['short_bid'])
    integer(value['lot_size'],1,100000)
    integer(value['quantity'],1,1000000)
    if value['quantity'] % value['lot_size']:
        raise ValueError('quantity must be a whole number of the supplied lot size')
    cost=value['round_trip_cost_inr']
    if cost is not None and (type(cost) not in (int,float) or not math.isfinite(cost) or cost < 0):
        raise ValueError('invalid cost assumption')
    budget=value.get('margin_budget_inr')
    if budget is not None:
        positive(budget)
    if not isinstance(value['hedges'],list) or not 1 <= len(value['hedges']) <= 20:
        raise ValueError('one to twenty specified hedges required')
    results=[]
    seen=set()
    for hedge in value['hedges']:
        keys(hedge,{'strike','ask','broker_margin_inr'})
        strike,ask=positive(hedge['strike']),positive(hedge['ask'])
        if strike in seen or strike <= short:
            raise ValueError('a distinct higher-strike call is required')
        seen.add(strike)
        margin=hedge['broker_margin_inr']
        if margin is not None:
            positive(margin)
        width,credit=strike-short,bid-ask
        if not 0 < credit < width:
            raise ValueError('not a valid positive-credit vertical spread')
        qty=value['quantity']
        results.append({'hedge_strike':strike,'width_points':width,'credit_points':credit,
            'gross_credit_inr':credit*qty,'gross_max_expiry_loss_inr':(width-credit)*qty,
            'net_max_profit_inr':credit*qty-cost if cost is not None else None,
            'net_max_expiry_loss_inr':(width-credit)*qty+cost if cost is not None else None,
            'gross_expiry_breakeven':short+credit,'broker_margin_inr':margin,
            'within_margin_budget':None if budget is None or margin is None else margin <= budget,
            'profit_rank_within_margin_budget':None,
            'profit_probability':None,'executable':False})
    # Rank supplied scenarios at the original fixed quantity. Unknown budget,
    # margin or costs cannot become a claim that a hedge fits the account.
    eligible=sorted((r for r in results if r['within_margin_budget'] is True
                     and r['net_max_profit_inr'] is not None and r['net_max_profit_inr'] > 0),
                    key=lambda r:(-r['net_max_profit_inr'],r['broker_margin_inr'],r['hedge_strike']))
    for rank,row in enumerate(eligible,1):
        row['profit_rank_within_margin_budget']=rank
    return {'status':'OWNER_REVIEW_ONLY','execution_enabled':False,'selected_hedge':None,
        'recommended_hedge_strike':eligible[0]['hedge_strike'] if eligible else None,
        'instrument':value['instrument'],'expiry':value['expiry'],'quantity':value['quantity'],
        'margin_budget_inr':budget, 'maximum_loss_limit_applied':False,
        'comparison_objective':'NET_MAX_EXPIRY_PROFIT_WITHIN_SUPPLIED_MARGIN_AT_FIXED_QUANTITY',
        'costs_verified':False,'metadata_and_quote_freshness_verified':False,'comparisons':results,
        'assumptions':['Equal quantities of calls on the same underlying, exchange and expiry',
            'Short filled at its bid and hedge bought at its ask; no actual fills are assumed',
            'Expiry payoff bound assumes both legs remain intact and settle as contracted',
            'Expiry maximum profit is not next-day expected profit or a forecast',
            'Broker margin is separate from maximum loss and can change',
            'Supplied costs/lot size are inputs, not broker-verified facts']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.input.stat().st_size > 32768:
        raise ValueError('input too large')
    result=compare(json.loads(args.input.read_text(encoding='utf-8')))
    with args.output.open('x',encoding='utf-8') as out:
        out.write(dumps(result))
    print(dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(dumps({'status':'BLOCKED','failure':safe_error(exc)}))
        raise SystemExit(2) from None

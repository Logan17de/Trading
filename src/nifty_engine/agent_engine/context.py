"""Deterministic, allowlisted accounting context for the data-only analyst."""
from __future__ import annotations

import json


def analysis_context(store, now):
    from .contracts import IST
    month = now.astimezone(IST).strftime("%Y-%m")
    rows = store.read("SELECT day,body FROM (SELECT day,body,ROW_NUMBER() OVER (PARTITION BY day ORDER BY at DESC,rowid DESC) AS n FROM snapshots WHERE day>=? AND day<=?) WHERE n=1",
                      (month+'-01', now.astimezone(IST).date().isoformat()))
    realized = []
    for row in rows:
        p = json.loads(row['body'])['portfolio']
        realized.append(p['realized_pnl'] if p['accounting_day'] == row['day'] else None)
    achieved = sum(realized) if realized and all(x is not None for x in realized) else None
    goal = store.meta('goal')
    target = goal.get('monthly_profit_target_inr', goal.get('target_inr')) if goal and goal['month'] == month else None
    marks = store.read("SELECT json_extract(body,'$.portfolio.realized_pnl') AS realized,json_extract(body,'$.portfolio.unrealized_pnl') AS unrealized,day FROM snapshots WHERE day>=? AND day<=? AND json_extract(body,'$.portfolio.accounting_day')=day ORDER BY at,rowid",
                       (month+'-01', now.astimezone(IST).date().isoformat()))
    totals, peak, drawdown, missing = {}, 0.0, 0.0, 0
    for row in marks:
        if row['realized'] is None or row['unrealized'] is None:
            missing += 1
            totals[row['day']] = row['realized']
            continue
        totals[row['day']] = row['realized']
        if any(x is None for x in totals.values()):
            missing += 1
            continue
        value = sum(totals.values()) + row['unrealized']
        peak = max(peak, value)
        drawdown = max(drawdown, peak-value)
    return {'monthly': {'month': month, 'monthly_profit_target_inr': target, 'achieved_recorded_inr': achieved,
                'remaining_inr': max(0,target-achieved) if target is not None and achieved is not None else None,
                'observed_days': len(rows), 'trading_days_remaining': None, 'required_average_per_day': None,
                'actual_expectancy': None, 'recorded_drawdown_inr': drawdown if marks else None,
                'unknown_mark_count': missing, 'coverage': 'RECORDED_DAYS_ONLY',
                'calendar_status': 'FULL_REMAINING_MONTH_NOT_VERIFIED', 'risk_escalation_allowed': False},
            'recent_triggers': store.read('SELECT reason,status,created,error FROM jobs ORDER BY created DESC LIMIT 12'),
            'engine_health': {'scheduler_heartbeat': store.meta('scheduler_heartbeat'),
                'worker_heartbeat': store.meta('worker_heartbeat'), 'last_ingest_error': store.meta('last_ingest_error')},
            'futures_state': None, 'futures_status': 'NOT_PUBLISHED'}

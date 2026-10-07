"""Current per-strategy readiness. Read-only; installed code is not live proof."""
import copy

from . import iv_history, report_strategies, strategy_controls


def assess(store, cfg, gate, observation, now):
    results={sid:dict(code_implemented=sid in strategy_controls.EXECUTABLE,
        live_ready=False,status='RESEARCH_ONLY' if sid=='calendar' else 'BLOCKED',indices={})
        for sid in strategy_controls.IDS}
    for index in cfg['indices']:
        bundle=copy.deepcopy(store.meta('report-strategy-evidence-'+index,{}))
        values=iv_history.features(store,index,now)
        features=bundle.setdefault('features',{})
        features.pop('iv30',None);features.pop('iv_percentile',None);features.update(values)
        review=report_strategies.evaluate(cfg,index,bundle,now,occupied=bool(store.read(
            "SELECT 1 FROM pc_orders WHERE state<>'CLOSED' LIMIT 1")))
        for row in review['strategies']:
            sid=row['id']
            blockers=list(row['reasons'])+gate.blockers(observation,purpose='ENTRY',strategy=sid)
            if not values:blockers.append('REVIEWED_MATCHED_IV_HISTORY_AND_CURRENT_REQUIRED')
            if sid=='calendar':blockers.append('CALENDAR_SETTLEMENT_PAYOFF_AND_MARGIN_MODEL_REQUIRED')
            ready=row['status']=='RESEARCH_CANDIDATE' and not blockers and results[sid]['code_implemented']
            results[sid]['indices'][index]=dict(live_ready=ready,research_status=row['status'],
                blockers=list(dict.fromkeys(blockers)))
            if ready:results[sid].update(live_ready=True,status='READY_FOR_ENTRY_RECHECK')
    return results

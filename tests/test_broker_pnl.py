from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine import broker_pnl
from nifty_engine.agent_engine.contracts import dumps
from nifty_engine.agent_engine.dashboard import DashboardCollector, view_model
from nifty_engine.agent_engine.pc_control import PcJournal

NOW = datetime(2026,10,5,5,tzinfo=timezone.utc)
SYMBOL = 'NIFTY26O0623000CE'


def position(quantity=65, entry=10, realized=0, symbol=SYMBOL):
    return dict(trading_symbol=symbol,exchange='NSE',segment='FNO',product='NRML',quantity=quantity,
        net_price=entry,realised_pnl=realized,credit_quantity=max(0,quantity),debit_quantity=max(0,-quantity),
        net_carry_forward_quantity=0,carry_forward_credit_quantity=0,carry_forward_debit_quantity=0,
        symbol_isin='PRIVATE_IDENTIFIER')


def option(quantity=65, price=12, symbol=SYMBOL):
    return dict(symbol=symbol,side='BUY' if quantity>0 else 'SELL',received_at=NOW.isoformat(),quote=dict(last_price=price))


def summary(rows, quotes=(), owners=None, **kwargs):
    return broker_pnl.summarize(rows,quotes,owners or {SYMBOL:'self'},now=NOW,
        received_at=NOW.isoformat(),complete=True,**kwargs)


def test_signed_marked_positions_closed_realized_and_bucket_reconciliation():
    short='NIFTY26O0622800CE'; closed='NIFTY26O0622650CE'
    end=position(0,79,-260,closed);end.update(credit_quantity=130,debit_quantity=130)
    result, legs=summary([position(realized=65),position(-65,20,0,short),end],
        [option(),option(-65,18,short)],{SYMBOL:'algo',short:'self',closed:'self'})
    assert result['realized_inr']==-195 and result['unrealized_inr']==260
    assert result['total_inr']==65 and result['closed_contracts']==1
    assert result['buckets']['algo']['total_inr']==195
    assert result['buckets']['self']['total_inr']==-130
    assert legs=={(SYMBOL,'BUY'):130,(short,'SELL'):130}
    assert 'PRIVATE_IDENTIFIER' not in dumps(result)
    assert result['basis']=='GROWW_GROSS_BEFORE_CHARGES'


@pytest.mark.parametrize('field,value,status',[
    ('net_carry_forward_quantity',65,'CARRY_DAY_BASIS_UNVERIFIED'),
    ('carry_forward_credit_quantity',65,'CARRY_DAY_BASIS_UNVERIFIED'),
    ('carry_forward_credit_quantity',None,'CARRY_DAY_BASIS_UNVERIFIED'),
    ('net_price',None,'QUOTE_UNAVAILABLE'),('realised_pnl',None,'INCOMPLETE'),
    ('credit_quantity',64,'INCOMPLETE'),('realised_pnl',float('nan'),'INCOMPLETE')])
def test_absent_inconsistent_and_overnight_accounting_is_never_invented(field,value,status):
    row=position();row[field]=value
    result,legs=summary([row],[option()])
    assert result['status']==status and result['total_inr'] is None and not legs


def test_no_trades_can_be_zero_but_failed_reads_and_duplicate_rows_cannot():
    result,_=summary([])
    assert result['status']=='AVAILABLE' and result['total_inr']==0
    assert result['buckets']['algo']['total_inr']==0
    failed,_=broker_pnl.summarize([],[],{},now=NOW,received_at=NOW.isoformat(),complete=False)
    assert failed['total_inr'] is None
    assert summary([position(),position()],[option()])[0]['status']=='INCOMPLETE'
    assert summary([position()],[])[0]['status']=='QUOTE_UNAVAILABLE'
    other=position(symbol='SOMESTOCK26O061000CE')
    assert summary([other],[])[0]['status']=='UNSUPPORTED_CONTRACT'


def test_pnl_unknown_ownership_is_separate_not_counted_as_self_or_algo():
    result,_=summary([position()],[option()],{SYMBOL:'unassigned'})
    assert result['buckets']['unassigned']['total_inr']==130
    assert result['buckets']['self']['total_inr']==result['buckets']['algo']['total_inr']==0


def test_current_totals_expire_day_rollover_and_public_schema_is_whitelisted():
    result,_=summary([position()],[option()])
    result['private']='PRIVATE_VALUE';result['buckets']['self']['broker_id']='PRIVATE_ID'
    assert 'PRIVATE' not in dumps(broker_pnl.public(result,NOW))
    for at in (NOW+timedelta(seconds=16),NOW-timedelta(seconds=1),NOW+timedelta(days=1)):
        assert broker_pnl.public(result,at)['total_inr'] is None
    result['total_inr']+=1
    assert broker_pnl.public(result,NOW)['status']=='INCOMPLETE'


def test_observed_pnl_history_survives_restart_has_null_failures_and_no_fake_backfill(tmp_path):
    journal=broker_pnl.PnlJournal(PcJournal(tmp_path/'private.sqlite').store)
    value,_=summary([position()],[option()])
    journal.record(value,NOW);journal.record(value,NOW)
    failed=broker_pnl.empty('QUOTE_UNAVAILABLE',(NOW+timedelta(seconds=5)).isoformat())
    journal.record(failed,NOW+timedelta(seconds=5))
    later=broker_pnl.PnlJournal(journal.store).series(NOW+timedelta(seconds=5))
    assert len(later)==2 and later[0]['self']==130 and later[1]['self'] is None
    assert journal.series(NOW-timedelta(seconds=1))==[]
    assert journal.series(NOW+timedelta(days=1))==[]


def test_exact_algorithm_attribution_closed_roundtrip_and_mixed_protection(tmp_path):
    journal=PcJournal(tmp_path/'private.sqlite')
    reference=journal.reserve('slot',SYMBOL,'BUY',65,strategy='EVERYDAY')
    journal.acknowledge(reference,'PRIVATE_BROKER_ID')
    owned=dict(trading_symbol=SYMBOL,order_reference_id=reference,groww_order_id='PRIVATE_BROKER_ID',
        transaction_type='BUY',quantity=65,filled_quantity=65)
    journal.ownership([owned],[position()],complete=True)
    assert journal.pnl_owners([owned],[position()],complete=True)[SYMBOL]=='algo'
    fake=dict(owned,groww_order_id='FAKE_BROKER')
    assert journal.pnl_owners([fake],[position()],complete=True)[SYMBOL]=='unassigned'
    assert journal.pnl_owners([owned],[position()],complete=False)[SYMBOL]=='unassigned'
    exit_ref=journal.reserve('slot',SYMBOL,'SELL',65,strategy='EVERYDAY')
    journal.acknowledge(exit_ref,'PRIVATE_EXIT')
    exit_order=dict(owned,order_reference_id=exit_ref,groww_order_id='PRIVATE_EXIT',transaction_type='SELL')
    closed=position(0,10,50);closed.update(credit_quantity=65,debit_quantity=65)
    journal.ownership([owned,exit_order],[closed],complete=True)
    assert journal.pnl_owners([owned,exit_order],[closed],complete=True)[SYMBOL]=='algo'
    manual=dict(owned,order_reference_id='GT_FAKE_PREFIX',groww_order_id='MANUAL')
    journal.ownership([owned,exit_order,manual],[closed],complete=True)
    assert journal.pnl_owners([owned,exit_order,manual],[closed],complete=True)[SYMBOL]=='unassigned'
    untouched=position(symbol='NIFTY26O0624000CE')
    assert journal.pnl_owners([],[untouched],complete=True)[untouched['trading_symbol']]=='self'


def test_collector_records_actual_pnl_and_failed_positions_clear_latest_totals(tmp_path):
    current=[NOW];fail=[False]
    def positions(**kwargs):
        if fail[0]:raise OSError('PRIVATE_ERROR')
        return dict(positions=[position()])
    broker=SimpleNamespace(get_quote=lambda **kwargs:{'last_price':12},
        get_order_list=lambda **kwargs:{'order_list':[]},get_positions_for_user=positions,
        get_historical_candles=lambda **kwargs:{'interval_in_minutes':5,'candles':[]})
    journal=PcJournal(tmp_path/'private.sqlite')
    collector=DashboardCollector(SimpleNamespace(groww=broker,limiter=SimpleNamespace(wait=lambda:None)),
        clock=lambda:current[0],background_history=False,journal=journal,
        calendar_loader=lambda:'',metadata_loader=lambda requested:{})
    first=collector.sample()
    assert first['broker_pnl']['buckets']['self']['total_inr']==130
    assert first['ordered_options'][0]['open_pnl_inr']==130
    current[0]+=timedelta(seconds=5);fail[0]=True
    last=collector.sample()
    assert last['broker_pnl']['total_inr'] is None and not last['ordered_options']
    lines=broker_pnl.PnlJournal(journal.store).series(current[0])
    assert len(lines)==2 and lines[-1]['self'] is None
    assert 'PRIVATE_ERROR' not in dumps(last)


def test_cached_dashboard_also_expires_active_leg_pnl(tmp_path,monkeypatch):
    import json
    from pathlib import Path
    from nifty_engine.agent_engine import dashboard
    current=[NOW]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return current[0].astimezone(tz) if tz else current[0].replace(tzinfo=None)
    monkeypatch.setattr(dashboard,'datetime',Clock)
    root=Path(__file__).parents[1]
    (tmp_path/'config').mkdir()
    (tmp_path/'config/owner_strategies.json').write_bytes((root/'config/owner_strategies.json').read_bytes())
    state=dashboard.DashboardState(tmp_path,offline=True)
    pnl,_=summary([position()],[option()])
    raw=dict(finished_at=NOW.isoformat(),broker_pnl=pnl,ordered_options=[dict(option(),index='NIFTY',
        quantity=65,order_status='POSITION',open_pnl_inr=130,pnl_bucket='self')])
    (state.directory/'market-check-pnl.json').write_text(json.dumps(raw),encoding='utf-8')
    assert state.read()['markets'][0]['options'][0]['open_pnl_inr']==130
    current[0]+=timedelta(seconds=16)
    cached=state.read()
    assert cached['broker_pnl']['total_inr'] is None
    assert cached['markets'][0]['options'][0]['open_pnl_inr'] is None

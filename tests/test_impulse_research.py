import copy
import json
from datetime import datetime,timedelta,timezone
import pytest

from nifty_engine.agent_engine.impulse_research import PremiumImpulseMonitor,config
from nifty_engine.agent_engine.store import Store

NOW=datetime(2026,10,6,3,45,tzinfo=timezone.utc)


def setup(tmp_path,index='NIFTY'):
    clock=[NOW];store=Store(tmp_path/'research.sqlite3')
    m=PremiumImpulseMonitor(store,clock=lambda:clock[0])
    members={index:dict(index=index,role='spot'),index+'F':dict(index=index,role='future')}
    for offset in range(-3,4):
        for kind in ('CE','PE'):
            s=f'{index}{offset}{kind}'
            members[s]=dict(index=index,role='option',strike=25000+offset*50,
                           expiry='2026-10-08',option_type=kind)
    m.configure(members)
    return m,clock,store


def send(m,clock,symbol,sec,price,**extra):
    clock[0]=NOW+timedelta(seconds=sec)
    m.ingest(dict(symbol=symbol,kind='price',provider_at=clock[0].timestamp(),price=price,**extra))


def warm(m,clock,index='NIFTY',kind='CE',jump=5):
    for sec in range(6):
        send(m,clock,index,sec,25000)
        send(m,clock,index+'F',sec,25000)
        for offset in range(-3,4):
            for k in ('CE','PE'):
                send(m,clock,f'{index}{offset}{k}',sec,100+(jump if sec==5 and offset==0 and k==kind else 0))
    return m.active[index]


@pytest.mark.parametrize('index,kind,threshold',[('NIFTY','CE',5),('NIFTY','PE',5),('SENSEX','CE',15),('SENSEX','PE',15)])
def test_ladders_both_indices_lock_and_no_orders(tmp_path,index,kind,threshold):
    m,clock,store=setup(tmp_path,index);e=warm(m,clock,index,kind,threshold)
    assert len(m.follow)==1 and len(e['locked_contracts'])==10
    assert e['direction']==('UP' if kind=='CE' else 'DOWN')
    assert str(threshold) in e['thresholds_crossed']
    assert e['lock_until']-e['event_start_timestamp']==30
    for method in (m.submit_order,m.modify_order,m.cancel_order):
        with pytest.raises(PermissionError,match='MONITOR_ONLY'):method()
    assert not m.public()['broker_writes']


def test_thresholds_enrich_one_event_and_atm_does_not_switch(tmp_path):
    m,clock,store=setup(tmp_path);e=warm(m,clock)
    original=e['event_id'];locked=copy.deepcopy(e['locked_contracts'])
    send(m,clock,'NIFTY',6,25050)
    send(m,clock,'NIFTY0CE',6,108)
    send(m,clock,'NIFTY1CE',6,125)
    send(m,clock,'NIFTY0CE',7,115)
    assert len(m.follow)==1 and m.active['NIFTY']['event_id']==original
    assert e['locked_contracts']==locked and e['locked_atm_strike']==25000
    assert set(e['thresholds_crossed'])=={'5','7.5','10','12.5','15'}
    assert e['thresholds_crossed']['7.5']['seconds_from_event_start']==1


def test_opposite_relationship_and_missing_fields(tmp_path):
    m,clock,store=setup(tmp_path);e=warm(m,clock)
    send(m,clock,'NIFTY0PE',6,90)
    send(m,clock,'NIFTY0CE',6,110)
    r=e['latest_relationships']
    assert r['leading_change']==10 and r['opposite_change']==-10
    assert r['directional_option_spread']==20
    assert r['expected_option_move'] is None
    assert r['neighbours']['NIFTY0CE']['iv'] is None
    assert r['neighbours']['NIFTY0CE']['cvd'] is None
    assert r['futures']['liquidity_status']=='NOT_VERIFIED'
    assert r['neighbours']['NIFTY0CE']['book'] is None


def test_duplicate_out_of_order_gap_and_opening_warmup(tmp_path):
    m,clock,store=setup(tmp_path)
    send(m,clock,'NIFTY',0,25000);send(m,clock,'NIFTY0CE',0,100)
    send(m,clock,'NIFTY0CE',0,200)
    clock[0]=NOW+timedelta(seconds=1)
    m.ingest(dict(symbol='NIFTY0CE',kind='price',provider_at=NOW.timestamp()-.1,price=200))
    assert m.rejected==2 and not m.active
    send(m,clock,'NIFTY0CE',1,130)
    assert not m.active  # no five-second opening history
    send(m,clock,'NIFTY0CE',10,170)
    assert not m.active and m._change('NIFTY0CE',clock[0].timestamp(),5) is None


def test_reconnect_and_restart_persistence_no_gap_bridges(tmp_path):
    m,clock,store=setup(tmp_path);e=warm(m,clock)
    m.flush();assert len(store.read('SELECT * FROM premium_impulse_events'))==1
    newer=PremiumImpulseMonitor(store,clock=lambda:clock[0])
    saved=json.loads(store.read('SELECT body FROM premium_impulse_events')[0]['body'])
    assert saved['followup_status']=='INTERRUPTED_RESTART' and not newer.active
    m.reset();m.flush()
    saved=json.loads(store.read('SELECT body FROM premium_impulse_events')[0]['body'])
    assert saved['data_gap'] and saved['followup_status']=='INTERRUPTED_RECONNECT'


def test_outcomes_actual_timestamps_known_denominators_and_false_flags(tmp_path):
    m,clock,store=setup(tmp_path);e=warm(m,clock)
    for sec in range(6,68):
        send(m,clock,'NIFTY',sec,25000+(sec-5)*2)
        send(m,clock,'NIFTYF',sec,25000+(sec-5)*2)
        m.tick()
    m.flush();rows=store.read('SELECT * FROM premium_impulse_outcomes WHERE horizon=5')
    o=json.loads(rows[0]['body'])
    assert o['status']=='OBSERVED' and o['maximum_favorable_move']==10
    assert o['maximum_adverse_move']==0 and o['continuation']['10'] is True
    assert e['continuation']['10']['seconds']==5
    report=m.report('2026-10-06',persist=True)
    group=next(r for r in report['groups'] if r['index']=='NIFTY' and r['option_type']=='CE' and r['threshold']=='5' and r['condition']=='ALL' and r['combination']=='impulse')
    p=group['outcomes']['5']['continuation']['10']
    assert p['known']==1 and p['continued']==1 and p['percent']==100
    assert p['status']=='INSUFFICIENT_SAMPLES' and p['wilson_95_percent'][0]<100
    assert len(store.read('SELECT * FROM daily_research_summary'))==1


def test_missing_future_endpoint_and_spot_gap_censor(tmp_path):
    m,clock,store=setup(tmp_path);e=warm(m,clock)
    send(m,clock,'NIFTY',11,25020)
    clock[0]=NOW+timedelta(seconds=12);m.tick();m.flush()
    o=json.loads(store.read('SELECT body FROM premium_impulse_outcomes WHERE horizon=5')[0]['body'])
    assert o['status']=='UNKNOWN' and o['maximum_favorable_move'] is None
    assert o['futures_change'] is None and all(v is None for v in o['continuation'].values())


def test_idle_close_lock_reselection_expiry_and_day_rollover(tmp_path):
    m,clock,store=setup(tmp_path);e=warm(m,clock)
    clock[0]=NOW+timedelta(seconds=34);m.tick();assert 'NIFTY' in m.active
    clock[0]=NOW+timedelta(seconds=36);m.tick();assert not m.active and e['status']=='CLOSED'
    # Master updates do not mutate the contract set of an old event.
    members=copy.deepcopy(m.members)
    for r in members.values():
        if r['role']=='option':r['expiry']='2026-10-09'
    m.configure(members)
    assert e['locked_expiry']=='2026-10-08'
    clock[0]=NOW+timedelta(days=1)
    m.ingest(dict(symbol='NIFTY',kind='price',provider_at=clock[0].timestamp(),price=25000))
    assert not m.follow and not m.active and m.day=='2026-10-07'
    assert e['followup_status']=='INTERRUPTED_DAY_ROLLOVER'


def test_one_second_snapshots_not_fake_ticks_and_raw_latency(tmp_path):
    m,clock,store=setup(tmp_path);warm(m,clock)
    m.tick();m.tick();m.flush()
    snapshots=store.read("SELECT body FROM premium_impulse_ticks WHERE kind='snapshot'")
    assert len(snapshots)==1
    r=json.loads(snapshots[0]['body'])
    assert r['exchange_timestamp'] is None and r['observations']['NIFTY']['exchange_timestamp']==clock[0].timestamp()
    assert r['metrics']['NIFTY0CE']['premium_change']['5']==5
    assert r['metrics']['NIFTY0CE']['velocity']['5']==1
    raw=json.loads(store.read("SELECT body FROM premium_impulse_ticks WHERE kind='price' LIMIT 1")[0]['body'])
    assert raw['receive_timestamp']==raw['processing_timestamp']==raw['exchange_timestamp']
    assert raw['open_interest'] is None


def test_config_no_trading_and_empty_reports_do_not_guess(tmp_path):
    with pytest.raises(ValueError,match='MONITOR_ONLY'):config(overrides={'trading_enabled':True})
    with pytest.raises(ValueError,match='TIMING'):config(overrides={'lock_seconds':10})
    m,clock,store=setup(tmp_path)
    report=m.report('2026-10-06')
    assert report['events']==0 and len(report['groups'])==120
    assert all(r['outcomes']['5']['continuation']['5']['percent'] is None for r in report['groups'])


def test_storage_failure_retains_raw_for_retry(tmp_path,monkeypatch):
    m,clock,store=setup(tmp_path);warm(m,clock)
    original=store.transaction
    def bad():raise OSError('fixture failure')
    monkeypatch.setattr(store,'transaction',bad)
    m.maintenance();assert m.failure and m.pending_ticks and m.dirty
    monkeypatch.setattr(store,'transaction',original)
    m.maintenance();assert not m.pending_ticks and not m.dirty and m.failure is None


def test_replay_uses_saved_custom_configuration_no_network(tmp_path,monkeypatch,capsys):
    from nifty_engine.agent_engine.impulse_research import main
    import sys
    m,clock,store=setup(tmp_path)
    warm(m,clock);m.tick();m.flush()
    output=tmp_path/'replay.sqlite3'
    monkeypatch.setattr(sys,'argv',['research','--database',str(store.path),'--day','2026-10-06','--replay-to',str(output)])
    main();summary=json.loads(capsys.readouterr().out)
    assert summary['events']==1 and not summary['broker_writes']
    raw=Store(output).read('SELECT body FROM premium_impulse_events')
    event=json.loads(raw[0]['body'])
    assert event['research_config_sha256']==m.config_sha256 and event['locked_atm_strike']==25000


def test_missing_exchange_time_saved_without_detection(tmp_path):
    m,clock,store=setup(tmp_path)
    m.ingest(dict(symbol='NIFTY0CE',kind='price',price=200))
    m.flush();r=json.loads(store.read('SELECT body FROM premium_impulse_ticks')[0]['body'])
    assert r['exchange_timestamp'] is None and not r['accepted'] and not m.active

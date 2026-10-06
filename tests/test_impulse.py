from datetime import datetime,timedelta,timezone
import copy
import pytest
from nifty_engine.agent_engine.impulse import ImpulseDetector,WEIGHTS
from nifty_engine.agent_engine.impulse_feed import packet,baseline_from_candles,universe
from nifty_engine.agent_engine.market_check import allowed_request


NOW=datetime(2026,10,6,4,tzinfo=timezone.utc)


def detector(index='NIFTY',kind='CE',baseline=True):
    u={'F':dict(index=index,role='future')}
    for offset,symbol in ((-1,'L'),(0,'ATM'),(1,'H')):
        u[symbol]=dict(index=index,role='option',offset=offset,option_type=kind,expiry='2026-10-08',strike=25000+offset*50)
    for n in range(5):u['C'+str(n)]=dict(index=index,role='constituent')
    clock=[NOW];d=ImpulseDetector(u,clock=lambda:clock[0])
    if baseline:d.baseline={(index,'13:00'):dict(days=5,volume=60),(index,'13:01'):dict(days=5,volume=60)}
    return d,clock


def warm(d,clock,direction=1,jump=14):
    for sec in range(61):
        clock[0]=NOW+timedelta(seconds=sec);at=clock[0].timestamp()
        d.ingest(dict(symbol='F',kind='price',provider_at=at,price=100+sec*.01*direction,volume=1000+sec+(500 if sec==60 else 0)))
        d.ingest(dict(symbol='F',kind='depth',provider_at=at,bid=100+sec*.01*direction,ask=101+sec*.01*direction,
                      bid_quantity=100 if direction>0 else 50,ask_quantity=50 if direction>0 else 100))
        for symbol in ('L','ATM','H'):
            d.ingest(dict(symbol=symbol,kind='price',provider_at=at,price=100+(jump if sec==60 else 0)))
            d.ingest(dict(symbol=symbol,kind='depth',provider_at=at,bid=99+sec*.01,ask=100+sec*.01,bid_quantity=100,ask_quantity=50))
        for n in range(5):d.ingest(dict(symbol='C'+str(n),kind='price',provider_at=at,price=100+sec*.01*direction))
    return d.public()['indices'][next(iter(d.universe.values()))['index']]


@pytest.mark.parametrize('index,kind,direction,jump',[('NIFTY','CE',1,14),('NIFTY','PE',-1,14),('SENSEX','CE',1,40),('SENSEX','PE',-1,40)])
def test_six_signals_confirm_on_callback_without_poll_sleep(index,kind,direction,jump):
    d,clock=detector(index,kind);r=warm(d,clock,direction,jump)
    assert sum(WEIGHTS.values())==100 and r['score']==100 and r['status']=='BREAKOUT_CONFIRMED'
    assert r['direction']==('UP' if direction>0 else 'DOWN')
    assert r['confirmation_elapsed_ms']<=3000 and not r['broker_writes'] and r['win_probability'] is None


def test_futures_disagree_does_not_label_iv_as_proven():
    d,clock=detector();r=warm(d,clock,direction=-1)
    assert r['status']=='FUTURES_DISAGREE_POSSIBLE_IV_OR_NOISE'
    assert r['signals']['futures_direction']['status']=='FAIL'


def test_missing_baseline_is_unknown_no_score_renormalization():
    d,clock=detector(baseline=False);r=warm(d,clock)
    assert r['status']=='EVIDENCE_UNKNOWN' and r['known_points']==85 and r['score']==85
    assert r['signals']['volume_acceleration']['status']=='UNKNOWN'


def test_neighbour_missing_blocks_confirmation_even_if_numeric_score_passes():
    d,clock=detector();warm(d,clock);d.prices['H'].clear()
    r=d.evaluate('NIFTY',clock[0].timestamp())
    assert r['score']==90 and r['status']=='EVIDENCE_UNKNOWN'


def test_old_duplicate_future_and_out_of_order_events_rejected():
    d,clock=detector();at=NOW.timestamp()
    valid=dict(symbol='F',kind='price',provider_at=at,price=100)
    d.ingest(valid);d.ingest(valid)
    for ts in (at-5,at+10,at-1):d.ingest(dict(valid,provider_at=ts))
    assert d.events==1 and d.rejected==4
    clock[0]+=timedelta(seconds=4)
    assert d.public()['indices']=={} and d.public()['status']=='STALE_OR_WAITING_FOR_STREAM'


def test_confirmation_deadline_does_not_extend_on_each_tick():
    d,clock=detector();warm(d,clock);first=d.candidates['NIFTY']['at']
    clock[0]+=timedelta(seconds=11)
    assert d.evaluate('NIFTY',clock[0].timestamp())['status']=='CONFIRMATION_WINDOW_EXPIRED'
    assert first==NOW.timestamp()+60


def test_changed_packet_only_and_timestamp_units_depth_numbers():
    registry={('NSE','FNO','123'):['ATM']}
    meta=dict(exchange='NSE',segment='FNO',feed_key='123',feed_type='ltp')
    data={'NSE':{'FNO':{'123':dict(tsInMillis=NOW.timestamp()*1000,ltp=20,volume='1234'),'456':dict(ltp=99)}}}
    r=packet(meta,data,registry)
    assert len(r)==1 and r[0]['provider_at']==NOW.timestamp() and r[0]['symbol']=='ATM'
    row=data['NSE']['FNO']['123'];row.update(buyBook={'1':dict(price=20,qty='50')},sellBook={'1':dict(price=21,qty='40')})
    assert packet(dict(meta,feed_type='market_depth'),data,registry)[0]['ask_quantity']=='40'


def test_baseline_requires_distinct_prior_days_and_actual_volume():
    rows=[]
    for days in range(1,6):
        at=NOW-timedelta(days=days)
        rows.append([at.timestamp(),1,2,1,2,100]);rows.append(rows[-1])
    rows.append([NOW.timestamp(),1,2,1,2,9999])
    result=baseline_from_candles({'candles':rows},NOW)
    assert result['13:00']==dict(days=5,volume=100)


def test_feed_auth_allowlist_never_grants_order_or_query_or_other_host():
    url='https://api.groww.in/v1/api/apex/v1/socket/token/create/'
    assert not allowed_request('POST',url)
    assert allowed_request('POST',url,feed=True)
    for candidate in (url+'?secret=x',url.replace('api.groww.in','evil.example'),'https://api.groww.in/v1/order/create'):
        assert not allowed_request('POST',candidate,feed=True)


def test_zero_default_volume_is_unknown_not_confirmed():
    d,clock=detector();warm(d,clock)
    d.prices['F']=type(d.prices['F'])((at,price,None) for at,price,v in d.prices['F'])
    r=d.evaluate('NIFTY',clock[0].timestamp())
    assert r['signals']['volume_acceleration']['status']=='UNKNOWN' and r['status']=='EVIDENCE_UNKNOWN'


def test_constituent_freshness_cannot_be_hidden_by_other_index_events():
    d,clock=detector();warm(d,clock)
    clock[0]+=timedelta(seconds=4)
    d.ingest(dict(symbol='C0',kind='price',provider_at=clock[0].timestamp(),price=102))
    r=d.public()['indices']['NIFTY']
    assert r['status']!='BREAKOUT_CONFIRMED'


def test_private_sdk_credentials_stay_out_of_package(tmp_path,monkeypatch):
    from nifty_engine.agent_engine.impulse_feed import StreamingImpulse
    from growwapi.common import files
    from types import SimpleNamespace
    monkeypatch.setattr(files,'generate_token_file',files.generate_token_file)
    monkeypatch.setattr(files,'generate_seed_file',files.generate_seed_file)
    from nifty_engine.agent_engine.store import Store
    service=StreamingImpulse(tmp_path,Store(tmp_path/'test.sqlite3'))
    service._private_credentials()
    import pathlib
    path=pathlib.Path(files.generate_token_file('synthetic-not-a-real-token'))
    assert path.parent==tmp_path/'.agent-state/feed-auth' and path.is_file()
    service._disconnect();assert not path.exists()


def test_browser_sse_uses_cached_read_only_stream_and_rejects_foreign_host():
    import threading,json,urllib.request
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace
    from nifty_engine.agent_engine.dashboard import handler
    stop=threading.Event();state=SimpleNamespace(token='synthetic-local-token',stop_event=stop,
        remote=SimpleNamespace(impulse_read=lambda:dict(format='trading-impulse-v1',indices={},broker_writes=False)))
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(state));t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}/api/impulse/events'
        with urllib.request.urlopen(url,timeout=2) as response:
            assert response.headers['Content-Type']=='text/event-stream'
            line=response.readline().decode();assert line.startswith('data: ')
            assert json.loads(line[6:])['broker_writes'] is False
        import urllib.error
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(urllib.request.Request(url,headers={'Host':'foreign.invalid'}),timeout=2)
        assert exc.value.code==403
    finally:stop.set();server.shutdown();server.server_close();t.join(timeout=2)

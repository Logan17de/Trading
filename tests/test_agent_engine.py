from __future__ import annotations

import copy
import json
import os
import sys
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from nifty_engine.agent_engine.__main__ import DEFAULT, load_config, paused, scheduler_tick, synthetic
from nifty_engine.agent_engine.contracts import IST, decision, snapshot, watch
from nifty_engine.agent_engine.reporting import build_report, deliver_once, queue_report, save_preview
from nifty_engine.agent_engine.runner import CodexRunner, work_once
from nifty_engine.agent_engine.store import Store

NOW = datetime(2026, 9, 30, 10, 0, tzinfo=IST)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'engine.sqlite')


def spec(**changes):
    return {"name": "watch", "instrument": "NIFTY", "metric": "spot", "op": "cross_above", "threshold": 24600,
        "cooldown_seconds": 300, "max_fires": 2, "expires_at": (NOW + timedelta(hours=6)).isoformat(), **changes}


def feed(store, at=NOW, spot=24500):
    s = synthetic(at, spot)
    store.ingest(s, at)
    return s


def trigger(store):
    store.add_watch(spec(), NOW)
    feed(store)
    assert store.evaluate(NOW) == 0
    now = NOW + timedelta(seconds=20)
    feed(store, now, 24700)
    assert store.evaluate(now) == 1
    return now


def answer(s, watches=None):
    return {"snapshot_id": s["id"], "action": "WAIT", "summary": "Evidence insufficient; no trade.",
        "evidence_ids": [], "watches": watches or [], "research_proposal": None}


@pytest.mark.parametrize("mutation", [
    lambda x: x.update(mode="LIVE"),
    lambda x: x.update(api_key="not-allowed"),
    lambda x: x['markets']['NIFTY'].update(spot=float('nan')),
    lambda x: x['markets']['NIFTY'].update(spot=True),
    lambda x: x['markets']['NIFTY'].update(spot=-1),
    lambda x: x.update(observed_at="2026-09-30T10:00:00"),
    lambda x: x['portfolio'].update(open_positions=-1),
    lambda x: x['markets']['NIFTY'].update(at=(NOW + timedelta(seconds=1)).isoformat()),
])
def test_snapshot_rejects_invalid(mutation):
    s = synthetic(NOW, 24500)
    mutation(s)
    with pytest.raises((ValueError, TypeError)): snapshot(s)


@pytest.mark.parametrize("changes", [{"op":"execute"}, {"instrument":"BTC"}, {"metric":"secret"},
    {"cooldown_seconds":1}, {"max_fires":100}, {"threshold":float('inf')},
    {"expires_at":(NOW-timedelta(seconds=1)).isoformat()}, {"command":"rm"}])
def test_watch_rejects_invalid(changes):
    with pytest.raises(ValueError): watch(spec(**changes), NOW)


def test_persistence_idempotence_and_collision(store):
    s = feed(store)
    assert store.ingest(s, NOW) is False
    fresh = Store(store.path)
    assert fresh.latest() == s
    bad = copy.deepcopy(s); bad['status'] = 'DIFFERENT'
    with pytest.raises(ValueError): store.ingest(bad, NOW)
    s['id'] = 'source-change'; s['source'] = 'other'
    with pytest.raises(ValueError): store.ingest(s, NOW)


def test_future_snapshot_blocked(store):
    with pytest.raises(ValueError): store.ingest(synthetic(NOW + timedelta(seconds=10), 24500), NOW)


def test_cross_is_not_initial_level(store):
    store.add_watch(spec(), NOW)
    feed(store, NOW, 24900)
    assert store.evaluate(NOW) == 0


def test_cross_restart_and_duplicate(store):
    store.add_watch(spec(), NOW)
    feed(store); store.evaluate(NOW)
    store = Store(store.path)
    now = NOW + timedelta(seconds=20)
    feed(store, now, 24700)
    assert store.evaluate(now) == 1
    assert store.evaluate(now) == 0
    assert len(store.read('SELECT * FROM jobs')) == 1


def test_stale_gap_does_not_manufacture_cross(store):
    store.add_watch(spec(), NOW)
    feed(store); store.evaluate(NOW)
    now = NOW + timedelta(minutes=10)
    feed(store, now, 24900)
    assert store.evaluate(now) == 0


def test_level_cooldown_and_cap(store):
    store.add_watch(spec(op='above', max_fires=1), NOW)
    feed(store, NOW, 24900)
    assert store.evaluate(NOW) == 1
    later = NOW + timedelta(minutes=10)
    feed(store, later, 25000)
    assert store.evaluate(later) == 0


def test_out_of_order_observations(store):
    feed(store, NOW + timedelta(seconds=20), 24900)
    feed(store)
    assert store.latest()['markets']['NIFTY']['spot'] == 24900


def test_single_claim_and_fencing(store):
    now = trigger(store)
    first = store.claim(now, timeout=5)
    assert first
    assert Store(store.path).claim(now, timeout=5) is None
    later = now + timedelta(seconds=40)
    second = store.claim(later, timeout=5)
    assert second and second['token'] != first['token']
    assert not store.finish(first, answer(first['snapshot']), later)
    assert store.finish(second, answer(second['snapshot']), later)


def test_stale_job_never_invoked(store):
    now = trigger(store)
    assert store.claim(now + timedelta(minutes=10)) is None
    assert store.read('SELECT status FROM jobs')[0]['status'] == 'EXPIRED'


def test_follow_up_is_durable_and_bounded(store):
    now = trigger(store)
    def analyst(payload):
        return answer(payload['snapshot'], [spec(name='follow', op='below', threshold=24600)])
    assert work_once(store, analyst, clock=lambda: now)
    store = Store(store.path)
    later = now + timedelta(seconds=20)
    feed(store, later, 24500)
    assert store.evaluate(later) == 1
    assert len(store.read('SELECT * FROM jobs')) == 2
    assert work_once(store, lambda payload: answer(payload['snapshot']), clock=lambda: later)


def test_invalid_model_actions_fail_closed(store):
    now = trigger(store)
    def analyst(payload):
        result = answer(payload['snapshot']); result['action'] = 'SELL'
        return result
    work_once(store, analyst, clock=lambda: now)
    assert store.read('SELECT status FROM jobs')[0]['status'] == 'FAILED'
    assert len(store.status()['watches']) == 1


def test_false_citation_rejected():
    result = answer(synthetic(NOW, 24500)); result['evidence_ids'] = ['made-up']
    with pytest.raises(ValueError): decision(result, result['snapshot_id'], set(), NOW)


def test_target_does_not_affect_trigger_or_budget(store):
    now = trigger(store)
    store.set_meta('goal', {'month':'2026-09','target_inr':1e9,'allocated_capital_inr':100})
    job = store.claim(now, daily_cap=1)
    store.finish(job, answer(job['snapshot']), now)
    later = now + timedelta(seconds=20)
    feed(store, later, 24000); store.evaluate(later)
    store.add_watch(spec(name='another', op='above', threshold=1), later)
    assert store.evaluate(later) == 1
    assert store.claim(later, daily_cap=1) is None


def test_missing_values_report_unknown_not_zero(store, tmp_path):
    bundle = build_report(store, '2026-09-30', NOW)
    assert 'Unknown' in bundle['mail']['text']
    assert 'NO_DATA' in bundle['mail']['text']
    assert 'INR 0.00' not in bundle['mail']['text']
    save_preview(bundle, tmp_path / 'mail')
    parsed = BytesParser(policy=policy.default).parsebytes((tmp_path/'mail/report.eml').read_bytes())
    assert any(p.get_content_type() == 'image/png' and p['Content-ID'] == '<daily-pnl>' for p in parsed.walk())
    assert (tmp_path/'mail/daily-pnl.png').read_bytes().startswith(b'\x89PNG')


def test_report_escapes_injection(store):
    s=synthetic(NOW,24500); s['reasons']=['<script>alert(1)</script>']; store.ingest(s,NOW)
    html=build_report(store,'2026-09-30',NOW)['mail']['html']
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_no_monthly_double_count(store):
    s=synthetic(NOW,24500,realized=100); store.ingest(s,NOW)
    later=NOW+timedelta(seconds=20);s=synthetic(later,24500,realized=150);store.ingest(s,later)
    store.set_meta('goal',{'month':'2026-09','target_inr':1000,'allocated_capital_inr':None})
    html=build_report(store,'2026-09-30',later)['mail']['html']
    assert 'INR 150.00' in html and 'INR 250.00' not in html


def setup_mail(store, monkeypatch):
    for name,value in [('RESEND_API_KEY','test'),('TRADING_REPORT_FROM','from@example.test'),('TRADING_REPORT_TO','to@example.test')]: monkeypatch.setenv(name,value)
    s=synthetic(NOW,24500);s['mode']='PAPER';store.ingest(s,NOW)
    return queue_report(store,build_report(store,'2026-09-30',NOW))


def test_email_queue_idempotence_and_receipt(store,monkeypatch):
    report_id=setup_mail(store,monkeypatch)
    assert queue_report(store,build_report(store,'2026-09-30',NOW))==report_id
    calls=[]
    def send(payload,key,identity): calls.append(identity); return 'provider-1'
    assert deliver_once(store,NOW,send=send)=='ACCEPTED'
    assert deliver_once(store,NOW,send=send)=='IDLE'
    assert calls==[report_id]
    assert store.status()['reports'][0]['status']=='ACCEPTED'


def test_mail_retry_reuses_immutable_payload(store,monkeypatch):
    setup_mail(store,monkeypatch); calls=[]
    def fail(payload,key,identity): calls.append(json.dumps(payload,sort_keys=True)); raise TimeoutError('secret')
    assert deliver_once(store,NOW,send=fail)=='RETRY_PENDING'
    monkeypatch.setenv('TRADING_REPORT_TO','changed@example.test')
    assert deliver_once(store,NOW+timedelta(seconds=61),send=fail)=='RETRY_PENDING'
    assert calls[0]==calls[1]
    assert store.status()['reports'][0]['error']=='TimeoutError'
    assert deliver_once(store,NOW+timedelta(hours=24),send=fail)=='REVIEW_REQUIRED'


def test_replay_cannot_email(store,monkeypatch):
    for name in ('RESEND_API_KEY','TRADING_REPORT_FROM','TRADING_REPORT_TO'):monkeypatch.setenv(name,'a@b.test')
    feed(store);queue_report(store,build_report(store,'2026-09-30',NOW))
    def forbidden(*args):raise AssertionError('must not call provider')
    assert deliver_once(store,NOW,send=forbidden)=='PREVIEW_ONLY'


def test_report_generated_without_codex(store,tmp_path):
    path=tmp_path/'snapshot.json';path.write_text(json.dumps(synthetic(NOW,24500)))
    cfg={**DEFAULT,'snapshot_file':str(path),'reports_dir':str(tmp_path/'reports')}
    later=NOW.replace(hour=16,minute=15)
    scheduler_tick(store,cfg,later)
    assert len(store.status()['reports'])==1
    assert (tmp_path/'reports/2026-09-30/report.html').is_file()
    scheduler_tick(store,cfg,later)
    assert len(store.status()['reports'])==1


def test_periodic_watch_starts_without_manual_watch(store,tmp_path):
    path=tmp_path/'snapshot.json';path.write_text(json.dumps(synthetic(NOW,24500)))
    cfg={**DEFAULT,'snapshot_file':str(path),'reports_dir':str(tmp_path/'reports')}
    scheduler_tick(store,cfg,NOW)
    assert len(store.read('SELECT * FROM jobs'))==1
    scheduler_tick(store,cfg,NOW)
    assert len(store.read('SELECT * FROM jobs'))==1


@pytest.mark.skipif(os.name!='posix',reason='Linux/WSL runner acceptance')
def test_real_subprocess_boundary_with_fake_codex(store,tmp_path,monkeypatch):
    now=trigger(store)
    script=tmp_path/'codex';home=tmp_path/'home';home.mkdir()
    script.write_text(f'''#!{sys.executable}
import json,os,sys
assert "RESEND_API_KEY" not in os.environ
assert "SUPABASE_SERVICE_ROLE_KEY" not in os.environ
assert "--sandbox" in sys.argv and "read-only" in sys.argv
assert "features.shell_tool=false" in sys.argv
p=json.loads(sys.stdin.read().splitlines()[-1])
result={{"snapshot_id":p["snapshot"]["id"],"action":"WAIT","summary":"Subprocess checked","evidence_ids":[],"watches":[],"research_proposal":None}}
open(sys.argv[sys.argv.index("--output-last-message")+1],"w").write(json.dumps(result))
print(json.dumps({{"type":"turn.completed"}}))
''')
    script.chmod(0o700)
    monkeypatch.setenv('RESEND_API_KEY','mail-secret');monkeypatch.setenv('SUPABASE_SERVICE_ROLE_KEY','db-secret')
    runner=CodexRunner(str(script),str(home),timeout=5)
    assert work_once(store,runner,clock=lambda:now)
    assert store.read('SELECT status FROM jobs')[0]['status']=='SUCCEEDED'


@pytest.mark.skipif(os.name!='posix',reason='Linux process timeout')
def test_runner_timeout_kills_child(tmp_path):
    script=tmp_path/'codex'; home=tmp_path/'home';home.mkdir()
    script.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(30)\n');script.chmod(0o700)
    with pytest.raises(TimeoutError):CodexRunner(str(script),str(home),timeout=0.1)({})


def test_runner_rejects_inherited_tools(tmp_path):
    executable=tmp_path/'codex';executable.write_text('');home=tmp_path/'home';home.mkdir()
    (home/'config.toml').write_text('[mcp_servers.broker]\ncommand="bad"')
    with pytest.raises(ValueError):CodexRunner(str(executable),str(home))


def test_repository_pause(tmp_path):
    cfg={**DEFAULT,'repository_root':str(tmp_path)}
    (tmp_path/'.trader-paused').write_text('paused')
    assert paused(cfg)


def test_config_rejects_unknown_and_malformed(tmp_path):
    path=tmp_path/'config.json'
    path.write_text(json.dumps({'enabled':'true'}))
    with pytest.raises(ValueError):load_config(path)
    path.write_text(json.dumps({'execute_orders':True}))
    with pytest.raises(ValueError):load_config(path)






def test_stale_result_does_not_install_watches(store):
    now=trigger(store)
    job=store.claim(now,timeout=400)
    result=answer(job['snapshot'],[spec(name='do-not-add')])
    assert store.finish(job,result,now+timedelta(seconds=301))
    assert store.read('SELECT status FROM jobs')[0]['status']=='STALE'
    assert len(store.status()['watches'])==1


def test_existing_watch_cannot_reset_counter(store):
    now=trigger(store)
    with pytest.raises(ValueError):store.add_watch(spec(threshold=24900),now)
    store.add_watch(spec(),now)
    assert store.status()['watches'][0]['fires']==1


def test_follow_up_depth_limit(store):
    with store.transaction() as db:
        with pytest.raises(ValueError):store._add_watch(db,spec(),NOW,root='test',depth=3)


def test_unknown_quote_gaps_not_flat_profit(store):
    s=synthetic(NOW,24500,realized=100,unrealized=None,open_positions=1)
    store.ingest(s,NOW)
    bundle=build_report(store,'2026-09-30',NOW)
    assert 'Open P&L: Unknown' in bundle['mail']['text']
    assert 'Open positions: 1' in bundle['mail']['text']


def test_failed_codex_does_not_prevent_report(store,tmp_path):
    now=trigger(store)
    def failed(payload):raise TimeoutError('provider-secret')
    work_once(store,failed,clock=lambda:now)
    bundle=build_report(store,'2026-09-30',now)
    save_preview(bundle,tmp_path/'email')
    assert 'TimeoutError' in bundle['mail']['html']
    assert 'provider-secret' not in bundle['mail']['html']


def test_active_watch_cap(store):
    for n in range(24):store.add_watch(spec(name=f'w{n}'),NOW)
    with pytest.raises(ValueError):store.add_watch(spec(name='overflow'),NOW)


def test_atomic_snapshot_rejects_unvalidated_data_without_overwriting(tmp_path):
    from nifty_engine.agent_engine.bridge import write_snapshot
    target=tmp_path/'latest.json'
    data=synthetic(NOW,24500,unrealized=None,open_positions=1)
    write_snapshot(target,data)
    before=target.read_bytes()
    with pytest.raises(ValueError):
        write_snapshot(target,{**data,'broker_secret':'must-not-export'})
    assert target.read_bytes()==before
    assert json.loads(before)['portfolio']['unrealized_pnl'] is None

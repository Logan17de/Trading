"""Synthetic checks for live-input latency; no broker credentials or writes."""
import copy
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine import normal_theta, report_data
from nifty_engine.agent_engine.broker_readiness import KEY
from nifty_engine.agent_engine.oracle_runtime import Runtime
from nifty_engine.agent_engine.pc_control import PcJournal
from test_broker_commissioning import Session
from test_report_data import CFG, NOW, snapshot


def test_setup_scan_overlaps_two_reads_covers_full_year_and_finds_old_order(tmp_path):
    s = Session(tmp_path)
    rendezvous = threading.Barrier(2)
    lock = threading.Lock()
    calls = []
    detail_reads = []
    active = 0
    maximum = 0

    def listing(**kw):
        nonlocal active, maximum
        with lock:
            calls.append(kw)
            active += 1
            maximum = max(maximum, active)
        rendezvous.wait(timeout=5)
        with lock: active -= 1
        # The oldest window is still essential to the conflict check.
        stop = datetime.fromisoformat(kw['end_date_time'])
        old = stop.date() < (s.now - timedelta(days=350)).date()
        return {'orders': [{'smart_order_id': 'old-parent'}] if old and kw['smart_order_type'] == 'GTT' else []}

    def detail(**kw):
        detail_reads.append(kw)
        return dict(smart_order_id='old-parent', trading_symbol=s.plan['contract']['symbol'], status='ACTIVE')

    s.broker.get_smart_order_list = listing
    s.broker.get_smart_order = detail
    with pytest.raises(ValueError, match='EXISTING_SMART_ORDER_PROTECTED'):
        s.commission.preview(s.plan)
    assert maximum == 2 and len(calls) == 28 and len(detail_reads) == 1
    for kind in ('GTT', 'OCO'):
        windows = sorted((datetime.fromisoformat(r['start_date_time']), datetime.fromisoformat(r['end_date_time']))
                         for r in calls if r['smart_order_type'] == kind)
        assert len(windows) == 14
        assert all(b-a == timedelta(days=28) for a, b in windows)
        assert all(left[1] == right[0] for left, right in zip(windows, windows[1:]))
    assert not s.broker.writes


@pytest.mark.parametrize('fault', ['shape', 'timeout', 'pagination'])
def test_scan_failure_never_becomes_empty_or_authorizes_submission(tmp_path, fault):
    s = Session(tmp_path)
    def listing(**kw):
        if fault == 'timeout': raise TimeoutError('synthetic unavailable')
        if fault == 'shape': return {'orders': None}
        return {'orders': [{'smart_order_id': 'same-id'} for _ in range(50)]}
    s.broker.get_smart_order_list = listing
    with pytest.raises((ValueError, TimeoutError)):
        s.commission.preview(s.plan)
    assert not s.broker.writes and not s.store.meta('broker-commissioning-latest-id')


def test_execution_refreshes_actual_funds_and_positions_without_research_worker(tmp_path):
    j = PcJournal(tmp_path/'j.sqlite3')
    root = Path(__file__).parents[1]
    runtime = Runtime.__new__(Runtime)
    runtime.output = tmp_path/'snapshot.json'
    runtime.execution_error = None
    runtime.state = SimpleNamespace(pnl_lines=SimpleNamespace(store=j.store))
    runtime.research_catalog = CFG
    runtime.normal_catalog = normal_theta.load(root)
    raw = snapshot()
    raw['finished_at'] = datetime.now(timezone.utc).isoformat()
    raw['funds'] = dict(status='AVAILABLE', received_at=raw['finished_at'],
                        option_buy_available_inr=20000, option_sell_available_inr=30000)
    runtime.output.write_text(json.dumps(raw))
    observed = []
    runtime.executor = SimpleNamespace(tick=lambda *args: observed.append(j.store.meta('normal-theta-evidence-NIFTY')))
    runtime.execution_step()
    assert observed[0]['funds'] == raw['funds']
    assert observed[0]['positions_complete'] and observed[0]['protected_symbols']
    raw['funds'] = {'status': 'UNAVAILABLE'}
    raw['positions_status'] = 'UNAVAILABLE'
    runtime.output.write_text(json.dumps(raw))
    runtime.execution_step()
    assert observed[1]['funds'] == raw['funds'] and not observed[1]['positions_complete']
    assert runtime.execution_error is None


def test_normal_inputs_are_published_before_slow_research_margin_work(tmp_path):
    j = PcJournal(tmp_path/'j.sqlite3')
    now = [NOW]
    cfg = dict(CFG, indices=['NIFTY'])
    normal = normal_theta.load(Path(__file__).parents[1])
    reader = report_data.ReportData(SimpleNamespace(), j, cfg, clock=lambda: now[0], normal_cfg=normal)
    reader.calendar = SimpleNamespace(check=lambda _: None)
    j.store.set_meta(KEY, {'at': NOW.isoformat()})
    reader.history = lambda *args: None
    reader.iv_monitor = lambda *args: None
    reader.expiries = lambda *args: {'expiries': ['2026-10-29', '2026-11-10', '2026-12-10']}
    reader.books = lambda index, expiry, protected: [dict(expiry=expiry, received_at=now[0].isoformat(),
                                                           greeks_received_at=now[0].isoformat())]
    checked = []
    def margins(index, books, selected=None):
        if selected is not None: return {'normal-margin': {'received_at': now[0].isoformat()}}
        # A slow research operation starts only after Normal is consumable.
        checked.append(copy.deepcopy(j.store.meta('normal-theta-evidence-NIFTY')))
        now[0] += timedelta(seconds=30)
        return {}
    reader.margins = margins
    reader.check(snapshot())
    assert checked[0]['contracts'] == []  # No trend evidence means no normal trade scan.
    assert checked[0]['margins'] == {}  # A read-only margin query is not needed without direction.
    final = j.store.meta('normal-theta-evidence-NIFTY')
    assert final['contracts'] == []  # The slow research pass cannot insert stale normal candidates.


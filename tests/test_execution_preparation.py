import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from nifty_engine.agent_engine import premium_strategy as p
from nifty_engine.agent_engine.execution import (PreparedOrderGateway, basket_key,
    rank_baskets, protective_stop, next_entry_step)
from nifty_engine.agent_engine.pc_control import JST, PcJournal, PcMonitor

ROOT = Path(__file__).parents[1]
CFG = p.load(ROOT)
NOW = datetime(2026, 10, 5, 14, 30, tzinfo=JST)


def contract(strike, bid, ask, *, index="NIFTY", kind="CE", expiry="2026-10-06"):
    return dict(symbol=f'{index}26O06{strike}{kind}', index=index, expiry=expiry, strike=strike,
        lot_size=65 if index == "NIFTY" else 20, tick_size=0.05, bid=bid, ask=ask,
        bid_quantity=1000, ask_quantity=1000, received_at=NOW.isoformat())


def fixture():
    short = contract(25100, 20, 20.05)
    h1 = contract(25300, 4.95, 5)
    h2 = contract(25400, 3.95, 4)
    rows = [short, h1, h2]
    funds = dict(received_at=NOW.isoformat(), option_buy_available_inr=2000,
                 option_sell_available_inr=40000)
    expiry = dict(status="CONFIRMED_CURRENT_MASTER", day_jst="2026-10-05", expiries=["2026-10-06"])
    margins = {}
    for h in (h1, h2):
        for lots in (1, 2):
            margins[basket_key(short, h, lots*65)] = dict(received_at=NOW.isoformat(),
                basket_requirement_inr=(20000 if h is h1 else 23000)*lots,
                hedge_requirement_inr=h['ask']*lots*65+20, round_trip_charges_inr=100)
    return rows, margins, funds, expiry


def test_rank_profit_after_costs_and_actual_margin_selects_one_or_two_lots():
    rows, margins, funds, expiry = fixture()
    result = rank_baskets(CFG, rows, margins, funds, expiry, NOW, complete=True)
    assert result['status'] == 'PREPARED'
    assert result['selected']['hedge']['strike'] == 25300
    assert result['selected']['lots'] == 2
    # The wider one-lot hedge yields more credit but cannot beat affordable two lots.
    funds['option_sell_available_inr'] = 25000
    result = rank_baskets(CFG, rows, margins, funds, expiry, NOW, complete=True)
    assert result['selected']['hedge']['strike'] == 25400 and result['selected']['lots'] == 1
    assert not result['broker_writes'] and result['selected']['net_max_expiry_loss_inr'] > 2000
    # The stop trigger is not a fabricated maximum spread-loss approval.


def test_unknown_costs_margin_stale_books_and_no_affordable_pair_never_select():
    rows, margins, funds, expiry = fixture()
    assert rank_baskets(CFG, rows, {}, funds, expiry, NOW, complete=True)['selected'] is None
    funds['option_sell_available_inr'] = 100
    assert rank_baskets(CFG, rows, margins, funds, expiry, NOW, complete=True)['selected'] is None
    assert rank_baskets(CFG, rows, margins, funds, expiry, NOW+timedelta(seconds=16), complete=True)['selected'] is None
    funds['option_sell_available_inr'] = 40000
    for m in margins.values():
        m['round_trip_charges_inr'] = None
    assert rank_baskets(CFG, rows, margins, funds, expiry, NOW, complete=True)['selected'] is None


def test_current_master_expiry_including_holiday_shift_and_late_session():
    rows, margins, funds, expiry = fixture()
    expiry['expiries'] = ['2026-10-05', '2026-10-06']  # Monday, not a hardcoded weekday.
    assert rank_baskets(CFG, rows, margins, funds, expiry, NOW, complete=True)['reason'] == 'ACTUAL_EXPIRY_DAY_SKIPPED'
    assert rank_baskets(CFG, rows, margins, funds, expiry, NOW, strategy='LATE_SESSION', complete=True)['selected'] is None
    now = NOW.replace(hour=18)
    rows = [dict(contract(k, 20 if k == 24850 else 4.95, 20.05 if k == 24850 else 5,
                          kind='PE', expiry='2026-10-05'), received_at=now.isoformat())
            for k in range(24600, 25301, 50)]
    short = next(r for r in rows if r['strike'] == 24850)
    hedge = next(r for r in rows if r['strike'] == 24700)
    margins = {basket_key(short, hedge, 65): dict(received_at=now.isoformat(), basket_requirement_inr=20000,
        hedge_requirement_inr=400, round_trip_charges_inr=100)}
    funds['received_at'] = now.isoformat(); expiry['spot'] = 25000
    result = rank_baskets(CFG, rows, margins, funds, expiry, now, strategy='LATE_SESSION', trend='UP', complete=True)
    assert result['selected']['short']['strike'] == 24850
    assert result['selected']['hedge']['strike'] < 24850
    assert result['selected']['short']['symbol'].endswith('PE')
    assert rank_baskets(CFG, rows, margins, funds, expiry, now, strategy='LATE_SESSION', trend='FLAT', complete=True)['selected'] is None


def test_manual_matches_skip_other_manual_symbols_protected_and_carry_occupies_slot():
    rows, margins, funds, expiry = fixture()
    active = [dict(symbol=rows[0]['symbol'], side='SELL', quantity=65, product='NRML'),
              dict(symbol=rows[2]['symbol'], side='BUY', quantity=65, product='NRML')]
    assert rank_baskets(CFG, rows, margins, funds, expiry, NOW, complete=True, active=active)['status'] == 'SKIP'
    active = [dict(active[0], quantity=130)]
    assert rank_baskets(CFG, rows, margins, funds, expiry, NOW, complete=True, active=active)['selected'] is None
    active = [dict(symbol='NIFTY26O0625000CE', ownership='ENGINE_VERIFIED')]
    assert rank_baskets(CFG, rows, margins, funds, expiry, NOW, complete=True, active=active)['status'] == 'OCCUPIED'


def test_news_retired_for_premium_policy_not_faked_low(tmp_path, monkeypatch):
    (tmp_path/'config').mkdir()
    for name in ('premium_strategy.json', 'pc_app.example.json', 'owner_strategies.json'):
        (tmp_path/'config'/name).write_bytes((ROOT/'config'/name).read_bytes())
    (tmp_path/'.trader-paused').touch()
    monitor = PcMonitor(tmp_path)
    monkeypatch.setattr(monitor.news, 'snapshot', lambda now: pytest.fail('retired news worker used'))
    protocol = json.loads((ROOT/'config/owner_strategies.json').read_text())
    result = monitor.tick(None, protocol, NOW)
    assert result['news']['risk'] == 'UNKNOWN'
    assert result['news']['status'] == 'RETIRED_BY_OWNER'
    assert 'NEWS_HIGH_UNKNOWN_OR_STALE' not in result['algo']['blockers']
    assert not p.readiness(CFG, NOW, news_risk='HIGH')['news_required']


def test_stop_uses_both_actual_fills_costs_quantity_and_tick():
    stop = protective_stop(20, 5, 65, 100, 0.05)
    assert stop['trigger_price'] == 28.8
    assert stop['price'] == 28.85 and not stop['loss_cap_guaranteed']
    assert stop['carry_protection_required']
    with pytest.raises(ValueError):
        protective_stop(20, 25, 130, 1900, 0.05)


class Broker:
    simulated = True

    def __init__(self):
        self.orders = {}; self.writes = []; self.timeout = False; self.cancel_timeout = False

    def place_order(self, **order):
        self.writes.append(copy.deepcopy(order))
        ref = order['order_reference_id']
        self.orders[ref] = dict(order, groww_order_id='fake-'+ref, filled_quantity=0,
                               average_fill_price=0, order_status='OPEN')
        self.orders[ref].pop('timeout')
        if self.timeout:
            raise TimeoutError('private provider data')
        return dict(groww_order_id='fake-'+ref, order_reference_id=ref, order_status='OPEN')

    def get_order_status_by_reference(self, **args):
        return self.orders[args['order_reference_id']]

    def get_order_detail(self, **args):
        return next(r for r in self.orders.values() if r['groww_order_id'] == args['groww_order_id'])

    def cancel_order(self, **args):
        self.writes.append(dict(action='CANCEL', **args))
        row = self.get_order_detail(**args)
        if self.cancel_timeout:
            raise TimeoutError()
        row['order_status'] = 'CANCELLED'
        return dict(groww_order_id=row['groww_order_id'], order_status='OPEN')


def gateway(tmp_path):
    journal = PcJournal(tmp_path/'j.sqlite3'); broker = Broker()
    p.set_intent(journal.store, True, NOW)
    clock = [NOW]
    g = PreparedOrderGateway(journal, broker, tmp_path/'.trader-paused', deployed=True, clock=lambda:clock[0])
    order = dict(trading_symbol='NIFTY26O0625300CE', exchange='NSE', transaction_type='BUY',
                 quantity=65, price=5, product='NRML', order_type='LIMIT')
    observation = dict(received_at=NOW.isoformat(), complete=True)
    return g, broker, order, observation, clock


def test_write_ahead_timeout_restart_reference_recovery_never_resubmits(tmp_path):
    g, broker, order, obs, _ = gateway(tmp_path)
    broker.timeout = True
    first = g.submit('hedge', 'basket', 'EVERYDAY', order, obs)
    assert first['status'] == 'RECONCILIATION_REQUIRED' and not first['filled']
    reopened = PreparedOrderGateway(PcJournal(tmp_path/'j.sqlite3'), broker, tmp_path/'.trader-paused', deployed=True, clock=lambda:NOW)
    assert reopened.submit('hedge', 'basket', 'EVERYDAY', order, obs)['reference'] == first['reference']
    assert len(broker.writes) == 1
    assert reopened.reconcile('hedge')['status'] == 'PENDING'
    broker.orders[first['reference']].update(filled_quantity=65, average_fill_price=4.95, order_status='EXECUTED')
    result = reopened.reconcile('hedge')
    assert result['status'] == 'FULLY_FILLED' and result['average_fill_price'] == 4.95
    assert len(broker.writes) == 1
    with pytest.raises(ValueError, match='OPERATION_ID_CONFLICT'):
        reopened.submit('hedge', 'basket', 'EVERYDAY', dict(order, price=5.05), obs)


def test_concurrent_duplicate_attempts_write_once(tmp_path):
    g, broker, order, obs, _ = gateway(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _:g.submit('hedge', 'basket', 'EVERYDAY', order, obs), range(4)))
    assert len(broker.writes) == 1
    assert len({r['reference'] for r in results}) == 1


@pytest.mark.parametrize('gate', ['pause', 'off', 'undeployed', 'stale', 'incomplete', 'outside', 'real'])
def test_all_write_gates_and_real_broker_disabled(tmp_path, gate):
    g, broker, order, obs, clock = gateway(tmp_path)
    if gate == 'pause': g.pause_file.touch()
    if gate == 'off': p.set_intent(g.journal.store, False, NOW)
    if gate == 'undeployed': g.deployed = False
    if gate == 'stale': clock[0] += timedelta(seconds=11)
    if gate == 'incomplete': obs['complete'] = False
    if gate == 'outside': clock[0] = NOW.replace(hour=19); obs['received_at'] = clock[0].isoformat()
    if gate == 'real': broker.simulated = False
    with pytest.raises(ValueError): g.submit('h', 'b', 'EVERYDAY', order, obs)
    assert broker.writes == []


def test_protected_manual_one_slot_unknown_and_changed_orders(tmp_path):
    g, broker, order, obs, _ = gateway(tmp_path)
    with g.journal.store.transaction() as db:
        db.execute('INSERT INTO pc_protected VALUES(?)', (order['trading_symbol'],))
    with pytest.raises(ValueError, match='PROTECTED'):
        g.submit('h', 'b', 'EVERYDAY', order, obs)
    order = dict(order, trading_symbol='NIFTY26O0625400CE')
    r = g.submit('h', 'b', 'EVERYDAY', order, obs)
    with pytest.raises(ValueError, match='ONE_ACTIVE'):
        g.submit('h2', 'b2', 'LATE_SESSION', order, obs)
    broker.orders[r['reference']]['price'] = 6
    assert g.reconcile('h')['status'] == 'RECONCILIATION_REQUIRED'
    assert g.cancel('h', obs)['status'] == 'RECONCILIATION_REQUIRED'
    assert len(broker.writes) == 1


def test_partial_fill_cancel_timeout_not_retried_and_racing_fill_accounted(tmp_path):
    g, broker, order, obs, _ = gateway(tmp_path)
    r = g.submit('h', 'b', 'EVERYDAY', dict(order, quantity=130), obs)
    row = broker.orders[r['reference']]
    row.update(filled_quantity=65, average_fill_price=5)
    broker.cancel_timeout = True
    assert g.cancel('h', obs)['status'] == 'PARTIAL'
    assert g.cancel('h', obs)['status'] == 'CANCEL_RECONCILIATION_REQUIRED'
    assert len(broker.writes) == 2
    row.update(filled_quantity=130, order_status='EXECUTED')
    assert g.reconcile('h')['status'] == 'FULLY_FILLED'
    assert len(broker.writes) == 2


def test_hedge_first_partial_fills_and_exact_persistent_protection():
    args = dict(complete=True, hedge_position=65, short_position=-65)
    assert next_entry_step(None, None, None, **args) == 'SUBMIT_BOUGHT_HEDGE'
    assert next_entry_step({'status':'PARTIAL'}, None, None, **args) == 'CANCEL_AND_RECONCILE_HEDGE'
    h = {'status':'FULLY_FILLED', 'filled_quantity':65}
    assert next_entry_step(h, None, None, **args) == 'SUBMIT_SHORT_UP_TO_FILLED_HEDGE'
    assert next_entry_step(h, {'status':'PARTIAL'}, None, **args) == 'CANCEL_AND_RECONCILE_SHORT_REMAINDER'
    s = dict(h)
    assert next_entry_step(h, s, None, **args) == 'CREATE_PERSISTENT_OWNED_SHORT_PROTECTION'
    assert next_entry_step(h, s, {'status':'ACKNOWLEDGED','quantity':65}, **args) == 'WAIT_FOR_PROTECTION_RECONCILIATION'
    assert next_entry_step(h, s, {'status':'VERIFIED_ACTIVE','quantity':65}, **args) == 'MONITOR_OWNED_BASKET'
    assert next_entry_step(h, s, None, **dict(args, short_position=-130)) == 'WAIT_FOR_EXCLUSIVE_COMPLETE_POSITIONS'
    assert next_entry_step(h, s, None, **args, same_contract_conflict=True) == 'WAIT_FOR_EXCLUSIVE_COMPLETE_POSITIONS'


def test_preparation_worker_uses_broker_calculations_never_order_methods(tmp_path):
    from types import SimpleNamespace
    from nifty_engine.agent_engine.execution_data import GrowwPreparation
    journal = PcJournal(tmp_path/'j.sqlite3')
    calls = []
    class DataBroker:
        def get_option_chain(self, **args):
            return dict(underlying_ltp=25000, strikes={str(k):{'CE':{'trading_symbol':f'NIFTY26O06{k}CE','ltp':v}}
                for k,v in ((25100,20),(25300,5),(25400,4))})
        def get_quote(self, **args):
            ask = 20.05 if '25100CE' in args['trading_symbol'] else 5 if '25300CE' in args['trading_symbol'] else 4
            return dict(bid_price=ask-0.05, offer_price=ask, bid_quantity=1000, offer_quantity=1000)
        def get_order_margin_details(self, **args):
            calls.append(args['orders'])
            return dict(total_requirement=1000, brokerage_and_charges=20)
        def get_available_margin_details(self, **args):
            return dict(clear_cash=50000, fno_margin_details={'option_buy_balance_available':50000,'option_sell_balance_available':50000})
        def place_order(self, **args):
            pytest.fail('read-only preparation placed an order')
        modify_order = cancel_order = create_smart_order = place_order
    text = 'trading_symbol,underlying_symbol,segment,exchange,expiry_date,strike_price,lot_size,tick_size\n'+''.join(
        f'NIFTY26O06{k}CE,NIFTY,FNO,NSE,2026-10-06,{k},65,0.05\n' for k in (25100,25300,25400))
    market = SimpleNamespace(groww=DataBroker(), limiter=SimpleNamespace(wait=lambda:None))
    worker = GrowwPreparation(market, journal, clock=lambda:NOW, master=lambda:text)
    _, _, _, expiry = fixture()
    snapshot = dict(finished_at=NOW.isoformat(), positions_status='AVAILABLE', orders_status='AVAILABLE',
                    expiry_evidence={'NIFTY':expiry}, ordered_options=[])
    result = worker.safe_check(snapshot, CFG)
    assert result['status'] == 'PREPARED'
    assert result['selected']['lots'] == 2 and result['selected']['hedge']['strike'] == 25400
    assert len(calls) == 12 and not result['globally_maximum_profit_verified']
    assert result['book_evidence'] == dict(sampled=3, protected=0, master_missing=0, invalid=0, valid=3, stale=0)
    assert result['calculation_evidence']['available'] == 4
    assert journal.store.meta('premium-preparation') == result
    # Real broker requests can take long enough to invalidate their original
    # quotes. Do not call this "unaffordable" or relax the 15-second quote gate.
    clock = [NOW]
    worker.clock = lambda:clock[0]
    original = market.groww.get_order_margin_details
    def slow_calculation(**kwargs):
        clock[0] += timedelta(seconds=2)
        return original(**kwargs)
    market.groww.get_order_margin_details = slow_calculation
    result = worker.safe_check(snapshot, CFG)
    assert result['reason'] == 'SHORT_BOOK_EXPIRED_DURING_CALCULATIONS'
    assert result['selected'] is None and result['book_evidence']['stale'] == 3
    assert result['calculation_evidence']['available'] == 4
    worker.clock = lambda:NOW
    market.groww.get_order_margin_details = original
    with journal.store.transaction() as db:
        db.execute('INSERT INTO pc_protected VALUES(?)', ('NIFTY26O0625100CE',))
    calls.clear()
    result = worker.safe_check(snapshot, CFG)
    assert result['reason'] == 'TARGET_SHORT_MANUAL_PROTECTED'
    assert result['book_evidence']['protected'] == 1 and not calls
    with journal.store.transaction() as db:
        db.execute('DELETE FROM pc_protected')
    market.groww.get_order_margin_details = lambda **kwargs: (_ for _ in ()).throw(ConnectionError('secret payload'))
    result = worker.safe_check(snapshot, CFG)
    assert result['selected'] is None and 'secret payload' not in json.dumps(result)


def test_read_only_guard_allows_only_opt_in_margin_calculation_not_orders():
    from nifty_engine.agent_engine.market_check import allowed_request
    calc = 'https://api.groww.in/v1/margins/detail/orders'
    assert not allowed_request('POST', calc)
    assert allowed_request('POST', calc, calculations=True)
    for path in ('order/create', 'order/modify', 'order/cancel', 'order-advance/create'):
        assert not allowed_request('POST', 'https://api.groww.in/v1/'+path, calculations=True, dashboard=True)
    assert not allowed_request('POST', 'https://example.test/v1/margins/detail/orders', calculations=True)


def test_trend_requires_actual_completed_closes_and_missing_is_unknown(tmp_path):
    from nifty_engine.agent_engine.execution_data import observed_trend
    from nifty_engine.agent_engine.session import SessionJournal
    journal = PcJournal(tmp_path/'j.sqlite3'); SessionJournal(journal.store)
    now = NOW.replace(hour=18, minute=0); edge = int(now.timestamp())
    for i, price in enumerate((25000,25010,25020)):
        at = edge-600+i*300-5
        body = {'markets':{'NIFTY':{'ok':True,'quote':{'last_price':price}}}}
        with journal.store.transaction() as db:
            db.execute('INSERT INTO pc_observations VALUES(?,?,?,?,?)', (str(i),at,'2026-10-05',1,json.dumps(body)))
    assert observed_trend(journal.store, 'NIFTY', now) == 'UP'
    assert observed_trend(journal.store, 'SENSEX', now) == 'UNKNOWN'
    assert observed_trend(journal.store, 'NIFTY', now+timedelta(minutes=5)) == 'UNKNOWN'

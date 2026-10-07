"""Instrument-master reuse preserves exact contracts and actual receipt age."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from nifty_engine.agent_engine import execution_data, report_data
from nifty_engine.agent_engine.pc_control import PcJournal
from test_report_strategies import NOW, CFG


HEADER = 'trading_symbol,underlying_symbol,segment,exchange,expiry_date,lot_size,tick_size,strike_price\n'
DATES = ('2026-10-06', '2026-10-22', '2026-11-05', '2026-11-12', '2027-01-20')


def symbol(index, expiry, kind):
    return index + expiry.replace('-', '') + '25000' + kind


def master_text(*, nifty_lot=65, sensex_lot=20, dates=DATES):
    rows = [f'{symbol(index, expiry, kind)},{index},FNO,{exchange},{expiry},{lot},.05,25000\n'
            for index, exchange, lot in (('NIFTY', 'NSE', nifty_lot), ('SENSEX', 'BSE', sensex_lot))
            for expiry in dates for kind in ('CE', 'PE')]
    return HEADER + ''.join(rows)


class Session:
    def __init__(self, tmp_path):
        self.now = NOW; self.text = master_text(); self.fetches = 0
        self.expiry_calls = []; self.chain_calls = []; self.quote_calls = []
        self.journal = PcJournal(tmp_path / 'journal.sqlite3')
        self.market = SimpleNamespace(limiter=SimpleNamespace(wait=lambda: None), groww=SimpleNamespace(
            get_expiries=self.expiries, get_option_chain=self.chain, get_quote=self.quote))
        self.reader = self.new_reader()

    def fetch(self):
        self.fetches += 1
        if isinstance(self.text, Exception): raise self.text
        return self.text

    def new_reader(self):
        return report_data.ReportData(self.market, self.journal, CFG, clock=lambda: self.now, master=self.fetch)

    def expiries(self, **kwargs):
        self.expiry_calls.append(kwargs)
        return {'expiries': [d for d in DATES if d.startswith(str(kwargs['year']))]}

    def chain(self, **kwargs):
        self.chain_calls.append(kwargs)
        return {'strikes': {'25000': {kind: dict(trading_symbol=symbol(kwargs['underlying'], kwargs['expiry_date'], kind),
            greeks=dict(iv=25, delta=.2 if kind == 'CE' else -.2, theta=-1)) for kind in ('CE', 'PE')}}}

    def quote(self, **kwargs):
        self.quote_calls.append(kwargs)
        return dict(last_price=5, bid_price=4.95, offer_price=5, bid_quantity=1000, offer_quantity=1000)


def test_csv_is_parsed_once_for_both_indices_expiry_books_and_iv(tmp_path, monkeypatch):
    s = Session(tmp_path); counts = dict(parses=0, rows=0)
    original = execution_data.csv.DictReader
    def counted(*args, **kwargs):
        counts['parses'] += 1
        for row in original(*args, **kwargs):
            counts['rows'] += 1
            yield row
    monkeypatch.setattr(execution_data.csv, 'DictReader', counted)
    for index in ('NIFTY', 'SENSEX'):
        evidence = s.reader.expiries(index, s.now)
        assert evidence['expiries'] == ['2026-10-22', '2026-11-05', '2026-11-12']
        for expiry in evidence['expiries']:
            books = s.reader.books(index, expiry, set())
            assert len(books) == 2 and {r['index'] for r in books} == {index}
            assert {r['lot_size'] for r in books} == ({65} if index == 'NIFTY' else {20})
        point = s.reader.iv_monitor(index, evidence, {'features': {'spot': {'value': 25000, 'observed_at': s.now.isoformat()}}})
        assert point['current']['index'] == index and not point['execution_feature']
        assert s.reader.expiries(index, s.now) == evidence
    assert counts == {'parses': 1, 'rows': 20} and s.fetches == 1
    assert s.reader.master_at == NOW.isoformat()


def test_catalog_keeps_metadata_validation_and_exact_index_expiry_isolation():
    text = master_text()
    # Wrong exchange/segment, invalid date/numeric fields and foreign symbols
    # remain excluded just as in the original single-expiry validator.
    text += 'NIFTY99925000CE,NIFTY,FNO,BSE,2026-11-05,65,.05,25000\n'
    text += 'NIFTY99825000CE,NIFTY,CASH,NSE,2026-11-05,65,.05,25000\n'
    text += 'NIFTY99725000CE,NIFTY,FNO,NSE,not-a-date,65,.05,25000\n'
    text += 'NIFTY99625000CE,NIFTY,FNO,NSE,2026-11-05,65,nan,25000\n'
    text += 'NIFTY99525000CE,NIFTY,FNO,NSE,2026-11-05,0,.05,25000\n'
    text += 'OTHER99525000CE,NIFTY,FNO,NSE,2026-11-05,65,.05,25000\n'
    catalog = execution_data.metadata_catalog(text)
    for index in ('NIFTY', 'SENSEX'):
        rows = execution_data.metadata(text, index, '2026-11-05')
        assert rows == catalog[(index, '2026-11-05')]
        assert set(rows) == {symbol(index, '2026-11-05', kind) for kind in ('CE', 'PE')}
    assert execution_data.metadata(text, 'BANKNIFTY', '2026-11-05') == {}
    assert execution_data.metadata(text, 'NIFTY', '2026-11-06') == {}


def test_restart_with_saved_fresh_expiries_loads_a_current_master(tmp_path):
    s = Session(tmp_path)
    saved = s.reader.expiries('NIFTY', s.now)
    s.now += timedelta(seconds=1)
    s.text = master_text(nifty_lot=50, dates=tuple(d for d in DATES if d != '2026-11-05'))
    restarted = s.new_reader()
    evidence = restarted.expiries('NIFTY', s.now)
    assert s.fetches == 2 and evidence['master_received_at'] != saved['master_received_at']
    assert '2026-11-05' not in evidence['expiries']
    assert {r['lot_size'] for r in restarted.books('NIFTY', '2026-11-12', set())} == {50}


def test_refresh_replaces_both_indices_atomically_and_removed_contracts_disappear(tmp_path):
    s = Session(tmp_path); s.reader.expiries('NIFTY', s.now)
    old = s.reader._master_cache
    s.now += timedelta(seconds=3601)
    s.text = master_text(nifty_lot=50, sensex_lot=15, dates=tuple(d for d in DATES if d != '2026-11-05'))
    assert {r['lot_size'] for r in s.reader.books('NIFTY', '2026-11-12', set())} == {50}
    assert {r['lot_size'] for r in s.reader.books('SENSEX', '2026-11-12', set())} == {15}
    assert s.reader._master_cache is not old and s.reader.master_at == s.now.isoformat() and s.fetches == 2
    assert not s.reader.contracts('NIFTY', '2026-11-05')[0]
    assert '2026-11-05' not in s.reader.expiries('NIFTY', s.now)['expiries']


@pytest.mark.parametrize('failure', [RuntimeError('synthetic offline'), 'invalid,master\n', HEADER])
def test_failed_refresh_never_serves_or_redates_stale_catalog(tmp_path, failure):
    s = Session(tmp_path); s.reader.expiries('NIFTY', s.now)
    old = s.reader._master_cache; old_at = s.reader.master_at
    s.now += timedelta(seconds=3601); s.text = failure
    with pytest.raises((RuntimeError, ValueError)): s.reader.books('NIFTY', '2026-11-05', set())
    with pytest.raises((RuntimeError, ValueError)): s.reader.expiries('NIFTY', s.now)
    assert s.reader._master_cache is old and s.reader.master_at == old_at
    assert not s.chain_calls and not s.quote_calls
    s.text = master_text(nifty_lot=50)
    assert {r['lot_size'] for r in s.reader.books('NIFTY', '2026-11-05', set())} == {50}


def test_expired_requested_contract_is_not_queried_or_relabelled(tmp_path):
    s = Session(tmp_path)
    with pytest.raises(ValueError, match='UNEXPIRED_MASTER'):
        s.reader.books('NIFTY', '2026-10-06', set())
    assert not s.chain_calls and not s.quote_calls


def test_master_receipt_is_before_parse_and_slow_parse_cannot_be_installed(tmp_path, monkeypatch):
    s = Session(tmp_path); original = report_data.metadata_catalog
    def delayed(text):
        result = original(text); s.now += timedelta(seconds=3601); return result
    monkeypatch.setattr(report_data, 'metadata_catalog', delayed)
    with pytest.raises(ValueError, match='EXPIRED_DURING_PARSE'):
        s.reader.ensure_master(s.now)
    assert s.reader._master_cache is None and s.reader.master_at is None


def test_book_read_crossing_master_ttl_does_not_reuse_fresh_quote_as_master_timestamp(tmp_path):
    s = Session(tmp_path); s.reader.ensure_master(s.now)
    old_at = s.reader.master_at; s.now += timedelta(seconds=3599)
    original = s.quote
    def delayed(**kwargs):
        s.now += timedelta(seconds=2); return original(**kwargs)
    s.market.groww.get_quote = delayed
    with pytest.raises(ValueError, match='CURRENT_INSTRUMENT_MASTER'):
        s.reader.books('NIFTY', '2026-11-05', set())
    assert s.reader.master_at == old_at and s.fetches == 1

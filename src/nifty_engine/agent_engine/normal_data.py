"""Current trading inputs only. No research, raw tick recorder or Supabase upload."""
import copy
from datetime import date, datetime, timezone

from .contracts import number
from .execution import fresh
from .pc_control import JST
from .dashboard import IST
from .report_data import ReportData
from .normal_theta import confirmed_trend, scheduled_index, entry_window


def connect_snapshot(store, snapshot, cfg, now):
    protected = {r['symbol'] for r in store.read('SELECT symbol FROM pc_protected')}
    for r in snapshot.get('execution_observation', {}).get('positions', []):
        if r.get('ownership') != 'ENGINE_VERIFIED' and isinstance(r.get('symbol'), str): protected.add(r['symbol'])
    for r in snapshot.get('ordered_options', []):
        if r.get('ownership') != 'ENGINE_VERIFIED' and isinstance(r.get('symbol'), str): protected.add(r['symbol'])
    for index in cfg['indices']:
        features = {}
        probe = snapshot.get('probes', {}).get(index+'_quote', {})
        try:
            price = number(probe['value']['last_price'])
            if probe.get('ok') is True and price > 0:
                features['spot'] = dict(value=price, unit='index_points', source='GROWW_INDEX_QUOTE', observed_at=probe['received_at'])
        except (ValueError, TypeError, KeyError): pass
        history = store.meta('report-history-status-'+index, {})
        if history.get('status') == 'AVAILABLE' and history.get('day') == now.astimezone(IST).date().isoformat():
            features.update({k:v for k,v in store.meta('report-daily-features-'+index, {}).items() if k in ('ma20','ma50','adx14')})
        cache = store.meta('normal-theta-contracts-'+index, {})
        store.set_meta('normal-theta-evidence-'+index, dict(index=index,
            received_at=snapshot.get('finished_at'),
            positions_complete=snapshot.get('positions_status')=='AVAILABLE' and snapshot.get('orders_status')=='AVAILABLE'
                and snapshot.get('execution_observation', {}).get('complete') is True,
            funds=copy.deepcopy(snapshot.get('funds', {})), protected_symbols=sorted(protected),
            expiry_evidence=store.meta('report-expiries-'+index, {}), features=features,
            contracts=cache.get('contracts', []), margins=cache.get('margins', {})))


class NormalData(ReportData):
    def __init__(self, market, journal, cfg, **kwargs):
        super().__init__(market, journal, cfg, normal_cfg=cfg, initialize_calendar=False, **kwargs)

    def check(self, snapshot):
        now = self.clock()
        statuses={}
        connect_snapshot(self.store, snapshot, self.cfg, now)
        for index in self.cfg['indices']:
            faults = []
            try: self.history(index, now)
            except Exception: faults.append('DAILY_HISTORY_UNAVAILABLE')
            # Re-read actual funds/positions after slow history calls.
            latest = self.snapshot_reader() if self.snapshot_reader else snapshot
            connect_snapshot(self.store, latest, self.cfg, self.clock())
            b = self.store.meta('normal-theta-evidence-'+index, {})
            scan = dict(status='WAIT', checked_at=self.clock().isoformat(), broker_writes=False)
            books, margins = [], {}
            try:
                expiry = self.expiries(index, self.clock())
                connect_snapshot(self.store, latest, self.cfg, self.clock())
                b = self.store.meta('normal-theta-evidence-'+index, {})
                scanning = entry_window(self.cfg, self.clock()) and index==scheduled_index(self.cfg, self.clock())
                trend = confirmed_trend(self.cfg, index, b, self.clock()) if scanning else 'UNKNOWN'
                direction = 'PE' if trend=='BULLISH' else 'CE' if trend=='BEARISH' else None
                scan['status'] = ('INDEX_NOT_SCHEDULED_TODAY' if index!=scheduled_index(self.cfg,self.clock()) else
                    'ENTRY_WINDOW_CLOSED' if not scanning else 'NO_CONFIRMED_TREND')
                if direction and fresh(b.get('received_at'), self.clock(), 10):
                    day = self.clock().astimezone(JST).date()
                    lo,hi = self.cfg['short_dte']
                    dates = [d for d in expiry['expiries'] if lo<=(date.fromisoformat(d)-day).days<=hi]
                    if dates:
                        books = self.books(index, dates[0], set(b['protected_symbols']), direction=direction)
                        margins = self.margins(index, books, self.cfg, direction=direction, funds=b.get('funds'))
                        scan = self.margin_checks.get('normal-'+index, scan)
                        # Refresh actual replies after slow margin calculations;
                        # never refresh a cached receipt's original timestamp.
                        if margins and any(not fresh(r['received_at'],self.clock(),5)
                                or not fresh(r['greeks_received_at'],self.clock(),5) for r in books):
                            self.chains.pop((index,dates[0]),None)
                            books=self.books(index,dates[0],set(b['protected_symbols']),direction=direction)
                    else: scan['status'] = 'NO_LISTED_NORMAL_EXPIRY'
            except Exception: faults.append('CURRENT_CONTRACT_INPUT_UNAVAILABLE')
            self.store.set_meta('normal-theta-contracts-'+index, dict(contracts=books, margins=margins, scan=scan))
            self.store.set_meta('normal-data-status-'+index, dict(status='PARTIAL' if faults else 'CURRENT',
                reasons=faults, checked_at=self.clock().isoformat(), broker_writes=False))
            statuses[index]=faults
        connect_snapshot(self.store, self.snapshot_reader() if self.snapshot_reader else snapshot, self.cfg, self.clock())
        return dict(status='FAILED' if any(statuses.values()) else 'CURRENT',
            checked_at=self.clock().isoformat(),input_failures=statuses,
            failure={'stage':'CURRENT_INPUT_READ_FAILED'} if any(statuses.values()) else None,broker_writes=False)

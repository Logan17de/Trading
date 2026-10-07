"""Read-only wiring from Groww snapshots to the report strategy studies.

No owner-intent or order methods. Daily OHLC is not an IV history, and a missing
event calendar never becomes clear. Books and Greeks retain independent times.
"""
from __future__ import annotations

import copy
import itertools
from datetime import date, datetime, timedelta, timezone

from .contracts import EXCHANGES, identity, number, stamp
from .dashboard import IST, download_instrument_text
from .execution import fresh, leg
from .execution_data import metadata
from .market_check import quote_summary
from .pc_control import JST
from . import report_strategies as policy


def daily_rows(payload, now):
    """Documented 1day exchange OHLC; omit the current incomplete session."""
    interval=payload.get('candle_interval')
    if (interval not in (None,'1day') or (interval is None and payload.get('interval_in_minutes')!=1440)
            or not isinstance(payload.get('candles'), list)):
        raise ValueError('DAILY_EXCHANGE_HISTORY_REQUIRED')
    rows=[]
    for raw in payload['candles'][:800]:
        if not isinstance(raw, list) or len(raw)<5:raise ValueError('DAILY_OHLC_REQUIRED')
        at=datetime.fromisoformat(raw[0]) if isinstance(raw[0],str) else datetime.fromtimestamp(number(raw[0]),timezone.utc)
        if at.tzinfo is None:at=at.replace(tzinfo=IST)  # Groww's documented exchange time
        day=at.astimezone(IST).date()
        if day>now.astimezone(IST).date():raise ValueError('FUTURE_DAILY_SESSION')
        if day==now.astimezone(IST).date():continue
        rows.append(dict(day=day.isoformat(),high=number(raw[2]),low=number(raw[3]),close=number(raw[4])))
    # Validate duplicates, bounds and indicator input before persisting anything.
    policy.historical_features(rows, None, now, source='GROWW_DAILY_OHLC')
    return rows


def connect_snapshot(store, snapshot, cfg, now, normal_cfg=None):
    """Cheap collector-side bridge; never refresh a saved receipt timestamp."""
    for index in cfg['indices']:
        key='report-strategy-evidence-'+index
        b=copy.deepcopy(store.meta(key,{}))
        b.update(index=index,received_at=snapshot.get('finished_at'),
            positions_complete=snapshot.get('positions_status')=='AVAILABLE'
                and snapshot.get('orders_status')=='AVAILABLE'
                and snapshot.get('execution_observation',{}).get('complete') is True,
            funds=copy.deepcopy(snapshot.get('funds',{})))
        protected={r['symbol'] for r in store.read('SELECT symbol FROM pc_protected')}
        for r in snapshot.get('ordered_options',[]):
            if r.get('ownership')!='ENGINE_VERIFIED' and isinstance(r.get('symbol'),str):protected.add(r['symbol'])
        for r in snapshot.get('execution_observation',{}).get('positions',[]):
            if r.get('ownership')!='ENGINE_VERIFIED' and isinstance(r.get('symbol'),str):protected.add(r['symbol'])
        b['protected_symbols']=sorted(protected)
        expiry=store.meta('report-expiries-'+index,{})
        b['expiry_evidence']=copy.deepcopy(expiry if expiry.get('day_jst')==now.astimezone(JST).date().isoformat()
            else snapshot.get('expiry_evidence',{}).get(index,{}))
        features=b.setdefault('features',{})
        features.pop('spot',None)
        probe=snapshot.get('probes',{}).get(index+'_quote',{})
        if probe.get('ok') is True:
            try:
                spot=number(probe['value']['last_price'])
                if spot>0:features['spot']=dict(value=spot,unit='index_points',source='GROWW_INDEX_QUOTE',observed_at=probe['received_at'])
            except (KeyError,TypeError,ValueError):pass
        history=store.meta('report-daily-features-'+index,{})
        for name in ('ma20','ma50','adx14','rv30'):
            if name in history:features[name]=copy.deepcopy(history[name])
        # Only reviewed, method-matched provider history/current observations
        # supply production IV features. Research proxy points stay separate.
        from .iv_history import features as matched_iv
        features.pop('iv30',None);features.pop('iv_percentile',None)
        features.update(matched_iv(store,index,now))
        b.setdefault('event_calendar',{'status':'UNKNOWN'})
        official=store.meta('report-official-event-calendar',None)
        if official is not None:
            from .event_calendar import assessment
            b['event_calendar']=assessment(official.get('sources',{}),now,cfg['short_dte'][1]-cfg['close_dte'])
        b.setdefault('contracts',[]);b.setdefault('margins',{})
        contracts=store.meta('report-contracts-'+index,None)
        if contracts is not None:
            b['contracts']=copy.deepcopy(contracts['contracts'])
            b['margins']=copy.deepcopy(contracts['margins'])
        b['input_connection']=dict(source='ORACLE_COLLECTOR',snapshot_received_at=b['received_at'],
            current_snapshot=bool(b['received_at'] and fresh(b['received_at'],now)),
            historical_features=list(history),sampled_contracts=len(b['contracts']),
            exact_margin_comparisons=len(b['margins']),broker_writes=False)
        store.set_meta(key,b)
        if normal_cfg:
            normal=copy.deepcopy(b)
            cache=store.meta('normal-theta-contracts-'+index,{})
            normal['contracts']=copy.deepcopy(cache.get('contracts',[]))
            normal['margins']=copy.deepcopy(cache.get('margins',{}))
            store.set_meta('normal-theta-evidence-'+index,normal)


class ReportData:
    """Independent bounded research reads using the collector's rate limiter."""
    def __init__(self,market,journal,cfg,*,clock=lambda:datetime.now(timezone.utc),master=download_instrument_text,snapshot_reader=None,normal_cfg=None):
        self.market,self.store,self.cfg=market,journal.store,cfg
        self.clock,self.master=clock,master
        self.snapshot_reader=snapshot_reader
        self.normal_cfg=normal_cfg
        self.master_text=None;self.master_at=None;self.next_history={}
        self.chains={}
        self.book_checks={}
        from .event_calendar import OfficialCalendar
        self.calendar=OfficialCalendar(self.store)

    def call(self,method,**kwargs):
        self.market.limiter.wait()
        return method(timeout=5,**kwargs)

    def history(self,index,now):
        day=now.astimezone(IST).date()
        saved=self.store.meta('report-history-status-'+index,{})
        if saved.get('day')==day.isoformat() and saved.get('status')=='AVAILABLE':return
        if now.timestamp()<self.next_history.get(index,0):return
        self.next_history[index]=now.timestamp()+3600
        raw=self.call(self.market.groww.get_historical_candles,exchange=EXCHANGES[index],segment='CASH',
            groww_symbol=EXCHANGES[index]+'-'+index,start_time=f'{day-timedelta(days=120)} 00:00:00',
            end_time=f'{day-timedelta(days=1)} 23:59:59',candle_interval='1day')
        at=self.clock();rows=daily_rows(raw,at)
        features=policy.historical_features(rows,None,at,source='GROWW_DAILY_OHLC')
        self.store.set_meta('report-daily-features-'+index,features)
        self.store.set_meta('report-history-status-'+index,dict(status='AVAILABLE',day=day.isoformat(),
            received_at=at.isoformat(),sessions=len(rows),source='GROWW_DAILY_OHLC',iv_history=False))

    def expiries(self,index,now):
        day=now.astimezone(JST).date();key='report-expiries-'+index
        saved=self.store.meta(key,{})
        if saved.get('day_jst')==day.isoformat() and fresh(saved['received_at'],now,3600):return saved
        dates=set();unavailable_years=[]
        years={day.year,(day+timedelta(days=90)).year}
        for year in sorted(years):
            try:
                raw=self.call(self.market.groww.get_expiries,exchange=EXCHANGES[index],underlying_symbol=index,year=year)
                dates.update(date.fromisoformat(d).isoformat() for d in raw['expiries'])
            except Exception:
                if year==day.year:raise
                unavailable_years.append(year)  # Current verified dates remain usable; no future dates invented.
        if self.master_at is None or not fresh(self.master_at,now,3600):
            self.master_text=self.master();self.master_at=self.clock().isoformat()
        matched=[d for d in sorted(dates) if day<=date.fromisoformat(d)<=day+timedelta(days=90)
            and metadata(self.master_text,index,d)]
        evidence=dict(status='CONFIRMED_CURRENT_MASTER' if matched else 'UNKNOWN_BLOCKED',
            day_jst=day.isoformat(),expiries=matched,received_at=self.clock().isoformat(),
            comparison_scope='INDIVIDUAL_API_AND_MASTER_INTERSECTION_UP_TO_90_DTE')
        evidence['unavailable_years']=unavailable_years
        self.store.set_meta(key,evidence)
        return evidence

    def chain(self,index,expiry):
        chain=self.call(self.market.groww.get_option_chain,exchange=EXCHANGES[index],underlying=index,expiry_date=expiry)
        at=self.clock().isoformat();self.chains[(index,expiry)]=(chain,at)
        return chain,at

    def iv_monitor(self,index,expiries,bundle):
        from . import iv_observations as iv
        now=self.clock();spot=bundle.get('features',{}).get('spot',{})
        if not fresh(spot['observed_at'],now,15):raise ValueError('CURRENT_SPOT_REQUIRED')
        target=now+timedelta(days=30)
        dates=sorted(expiries['expiries'])
        lower=[d for d in dates if datetime.combine(date.fromisoformat(d),datetime.min.time(),IST).replace(hour=15,minute=30)<=target]
        upper=[d for d in dates if datetime.combine(date.fromisoformat(d),datetime.min.time(),IST).replace(hour=15,minute=30)>=target]
        if not lower or not upper:raise ValueError('ACTUAL_30_DAY_BRACKET_REQUIRED')
        terms=[]
        for expiry in dict.fromkeys((lower[-1],upper[0])):
            chain,at=self.chain(index,expiry)
            rows=metadata(self.master_text,index,expiry)
            terms.append(iv.term(index,expiry,spot['value'],chain,rows,at,self.clock()))
        now=self.clock()
        # Network delays must not make the independently received spot fresh.
        if not fresh(spot['observed_at'],now,15):raise ValueError('SPOT_EXPIRED_DURING_CHAIN_READ')
        return iv.save(self.store,iv.interpolate(index,terms,now),now)

    def books(self,index,expiry,protected):
        rows=metadata(self.master_text,index,expiry)
        cached=self.chains.get((index,expiry))
        chain,greek_at=cached if cached and fresh(cached[1],self.clock(),10) else self.chain(index,expiry)
        candidates=[]
        for strike,sides in chain.get('strikes',{}).items():
            for kind in ('CE','PE'):
                r=sides.get(kind,{})
                try:
                    symbol=r['trading_symbol'];delta=number(r['greeks']['delta']);iv=number(r['greeks']['iv'])
                    if symbol not in rows or symbol in protected or float(strike)!=rows[symbol]['strike']:continue
                    if not(0<delta<1 if kind=='CE' else -1<delta<0) or iv<=0:continue
                    candidates.append(dict(rows[symbol],delta=delta,iv=iv,iv_unit='annualized_percent',greeks_received_at=greek_at))
                    # Keep provider theta separate from any historical IV feature.
                    # Public API docs don't specify a day/unit convention. Use
                    # only a same-provider sign comparison, never INR/day income.
                    try:
                        candidates[-1].update(theta=number(r['greeks']['theta']),theta_unit='groww_native')
                    except (KeyError,ValueError,TypeError):pass
                except (KeyError,ValueError,TypeError):continue
        wanted={}
        for kind in ('CE','PE'):
            side=[r for r in candidates if r['symbol'].endswith(kind)]
            shorts=sorted((r for r in side if .15<=abs(r['delta'])<=.25),key=lambda r:abs(abs(r['delta'])-.2))[:1]
            for short in shorts:
                wanted[short['symbol']]=short
                hedges=sorted((r for r in side if (r['strike']>short['strike'] if kind=='CE' else r['strike']<short['strike'])),
                    key=lambda r:r['strike'] if kind=='CE' else -r['strike'])
                for r in hedges[:2]:wanted[r['symbol']]=r
            atm=sorted((r for r in side if .45<=abs(r['delta'])<=.55),key=lambda r:abs(abs(r['delta'])-.5))[:1]
            for r in atm:wanted[r['symbol']]=r
        books=[]
        check=dict(expiry=expiry,signed_greek_contracts=len(candidates),sampled_quotes=len(wanted),
            available_books=0,missing_depth=0,invalid_books=0,read_failures=0,broker_writes=False)
        for row in list(wanted.values())[:8]:
            try:
                raw=self.call(self.market.groww.get_quote,exchange=EXCHANGES[index],segment='FNO',trading_symbol=row['symbol'])
                at=self.clock();q=quote_summary(raw,at)
                if any(q.get(k) is None or q[k]<=0 for k in ('bid_price','offer_price','bid_quantity','offer_quantity')):
                    check['missing_depth']+=1
                    continue
                # Contract validation is deliberately strict. Greeks have their
                # own receipt clock and must not enter the book-only schema.
                base={k:row[k] for k in ('symbol','index','expiry','strike','lot_size','tick_size')}
                book=leg(dict(base,bid=q['bid_price'],ask=q['offer_price'],bid_quantity=q['bid_quantity'],ask_quantity=q['offer_quantity'],received_at=at.isoformat()))
                books.append(dict(book,delta=row['delta'],iv=row['iv'],iv_unit=row['iv_unit'],greeks_received_at=greek_at,
                    **{k:row[k] for k in ('theta','theta_unit') if k in row}))
            except (ValueError,TypeError,KeyError):check['invalid_books']+=1
            except Exception:check['read_failures']+=1
        check.update(available_books=len(books),checked_at=self.clock().isoformat())
        self.book_checks[(index,expiry)]=check
        return books

    def margins(self,index,books,cfg=None):
        cfg=cfg or self.cfg;now=self.clock();day=now.astimezone(JST).date()
        front=[r for r in books if cfg['short_dte'][0]<=(date.fromisoformat(r['expiry'])-day).days<=cfg['short_dte'][1]]
        puts=list(policy._pairs(front,'PE',cfg));calls=list(policy._pairs(front,'CE',cfg))
        condors=[] if cfg.get('format')=='normal-theta-v1' else [p+c for p,c in itertools.product(puts,calls) if p[1][0]['strike']<c[1][0]['strike'] and p[0][0]['expiry']==c[0][0]['expiry']]
        margins={};attempts=0
        for legs in puts+calls+condors:
            if len({r['lot_size'] for r,_ in legs})!=1:continue
            width=max(abs(legs[i][0]['strike']-legs[i+1][0]['strike']) for i in range(0,len(legs),2))
            credit=sum(r['bid'] if side=='SELL' else -r['ask'] for r,side in legs)
            for lots in (1,2):
                qty=legs[0][0]['lot_size']*lots
                if attempts>=6:return margins
                if not 0<credit<width or (width-credit)*qty>cfg['risk_limit_inr']:continue
                if any(not fresh(r['received_at'],self.clock()) or not fresh(r['greeks_received_at'],self.clock())
                    or (r['ask_quantity'] if side=='BUY' else r['bid_quantity'])<qty for r,side in legs):continue
                attempts+=1
                def order(r,side):
                    return dict(trading_symbol=r['symbol'],exchange=EXCHANGES[index],product='NRML',transaction_type=side,
                        quantity=qty,order_type='LIMIT',price=r['ask'] if side=='BUY' else r['bid'])
                try:
                    entry=self.call(self.market.groww.get_order_margin_details,segment='FNO',orders=[order(r,side) for r,side in legs])
                    hedge=self.call(self.market.groww.get_order_margin_details,segment='FNO',orders=[order(r,side) for r,side in legs if side=='BUY'])
                    exit=self.call(self.market.groww.get_order_margin_details,segment='FNO',orders=[order(r,'BUY' if side=='SELL' else 'SELL') for r,side in legs])
                    requirement=number(entry['total_requirement']);cost=number(entry['brokerage_and_charges'])+number(exit['brokerage_and_charges'])
                    if requirement<0 or cost<0:continue
                    key=identity({'legs':[(r['symbol'],side) for r,side in legs],'quantity':qty})
                    margins[key]=dict(received_at=self.clock().isoformat(),basket_requirement_inr=requirement,
                        hedge_requirement_inr=number(hedge['total_requirement']),round_trip_charges_inr=cost)
                except Exception:continue
        return margins

    def check(self,snapshot):
        now=self.clock();connect_snapshot(self.store,snapshot,self.cfg,now)
        if not any(self.store.meta('report-strategy-evidence-'+i,{})['input_connection']['current_snapshot'] for i in self.cfg['indices']):
            for i in self.cfg['indices']:
                self.store.set_meta('report-data-status-'+i,dict(status='WAIT',reason='CURRENT_COLLECTOR_SNAPSHOT_REQUIRED',at=now.isoformat(),broker_writes=False))
            return
        self.calendar.check(now)
        from .broker_readiness import KEY as audit_key,inspect_gtt
        previous=self.store.meta(audit_key,{})
        if not previous.get('at') or not fresh(previous['at'],self.clock(),1800):
            self.store.set_meta(audit_key,inspect_gtt(lambda **kw:self.call(self.market.groww.get_smart_order_list,**{k:v for k,v in kw.items() if k!='timeout'}),self.clock()))
        now=self.clock();connect_snapshot(self.store,self.snapshot_reader() if self.snapshot_reader else snapshot,self.cfg,now)
        for index in self.cfg['indices']:
            key='report-strategy-evidence-'+index;b=self.store.meta(key,{})
            if not b['input_connection']['current_snapshot']:
                self.store.set_meta('report-data-status-'+index,dict(status='WAIT',reason='CURRENT_COLLECTOR_SNAPSHOT_REQUIRED',at=now.isoformat(),broker_writes=False));continue
            faults=[]
            try:self.history(index,now)
            except Exception:faults.append('DAILY_HISTORY_UNAVAILABLE')
            try:
                expiry=self.expiries(index,now);day=now.astimezone(JST).date()
                if self.snapshot_reader:
                    connect_snapshot(self.store,self.snapshot_reader(),self.cfg,self.clock())
                    b=self.store.meta(key,{})
                try:self.iv_monitor(index,expiry,b)
                except Exception:faults.append('MATCHED_IV30_MONITOR_UNAVAILABLE')
                chosen=[]
                for lo,hi in (self.cfg['short_dte'],self.cfg['calendar_long_dte']):
                    dates=[d for d in expiry['expiries'] if lo<=(date.fromisoformat(d)-day).days<=hi]
                    if dates:chosen.append(dates[0])
                    elif (lo,hi)==tuple(self.cfg['short_dte']):faults.append('LISTED_30_45_DTE_SHORT_UNAVAILABLE')
                normal_dates=[]
                if self.normal_cfg:
                    lo,hi=self.normal_cfg['short_dte']
                    normal_dates=[d for d in expiry['expiries'] if lo<=(date.fromisoformat(d)-day).days<=hi][:1]
                books=[]
                all_books=[]
                for d in dict.fromkeys(normal_dates+chosen):all_books.extend(self.books(index,d,set(b['protected_symbols'])))
                books=[r for r in all_books if r['expiry'] in chosen]
                margins=self.margins(index,books)
                # Separate worker cache avoids overwriting a simultaneous collector refresh.
                self.store.set_meta('report-contracts-'+index,dict(contracts=books,margins=margins))
                if self.normal_cfg:
                    normal_books=[r for r in all_books if r['expiry'] in normal_dates]
                    normal_margins=self.margins(index,normal_books,self.normal_cfg)
                    self.store.set_meta('normal-theta-contracts-'+index,dict(contracts=normal_books,margins=normal_margins))
                if not chosen:faults.append('LISTED_30_45_OR_60_90_DTE_CONTRACTS_UNAVAILABLE')
                elif not books:faults.append('CURRENT_SIGNED_GREEKS_AND_BOOKS_REQUIRED')
                elif not margins:faults.append('CURRENT_AFFORDABLE_BASKET_MARGIN_UNAVAILABLE')
            except Exception:
                faults.append('CONTRACT_RESEARCH_READ_UNAVAILABLE')
                self.store.set_meta('report-contracts-'+index,dict(contracts=[],margins={}))
                if self.normal_cfg:self.store.set_meta('normal-theta-contracts-'+index,dict(contracts=[],margins={}))
                self.store.set_meta('report-expiries-'+index,{'status':'UNKNOWN_BLOCKED'})
            self.store.set_meta('report-data-status-'+index,dict(status='PARTIAL' if faults else 'CONNECTED',
                reasons=faults,at=self.clock().isoformat(),broker_writes=False,
                book_checks=[v for (i,_),v in self.book_checks.items() if i==index],
                iv_history='MATCHED_RESEARCH_COLLECTION_NOT_EXECUTION_HISTORY',
                event_calendar=self.store.meta('report-official-event-calendar',{}).get('status','UNKNOWN')))
            from .research_campaign import observe
            now=self.clock()
            connect_snapshot(self.store,self.snapshot_reader() if self.snapshot_reader else snapshot,self.cfg,now,self.normal_cfg)
            observe(self.store,index,self.store.meta(key,{}),now)

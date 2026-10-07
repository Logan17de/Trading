"""Bounded actual Groww basket preparation. No order placement or modification.

Runs independently of the five-second collector, sharing its rate limiter.
The full chain/master identifies contracts; only a bounded sample is compared.
No sampled comparison is advertised as the global maximum-profit hedge.
"""
from __future__ import annotations
import csv
import io
import json
from datetime import datetime, timezone

from .contracts import EXCHANGES, number
from .dashboard import download_instrument_text, clean_date, funds_summary
from .execution import leg, rank_baskets, basket_key, fresh
from .market_check import quote_summary, safe_error
from .pc_control import JST, SYMBOL
from . import premium_strategy as p


def observed_trend(store, index, now):
    """Three consecutive completed five-minute reception-price closes.

    Require a quote within the last 15 seconds of each completed interval. These
    are journalled observations, not assumed exchange bar timestamps.
    """
    end = int(now.timestamp())//300*300
    closes = []
    for edge in (end-600, end-300, end):
        rows = store.read('SELECT body FROM pc_observations WHERE at>=? AND at<? ORDER BY at DESC LIMIT 4',
                          (edge-15, edge))
        value = None
        for row in rows:
            market = json.loads(row['body']).get('markets', {}).get(index, {})
            quote = market.get('quote') or {}
            if market.get('ok') is True and quote.get('last_price') is not None:
                price = number(quote['last_price'])
                if price > 0:
                    value = price; break
        if value is None:
            return 'UNKNOWN'
        closes.append(value)
    return 'UP' if all(a<b for a,b in zip(closes, closes[1:])) else 'DOWN' if all(a>b for a,b in zip(closes, closes[1:])) else 'FLAT'


def metadata_catalog(text):
    """Validate the master once, keyed by exact index and expiry.

    This is a parsed lookup, not freshness evidence. Its caller must retain the
    original receipt time and refuse stale or failed refreshes.
    """
    catalog = {}
    for r in csv.DictReader(io.StringIO(text)):
        index = r.get('underlying_symbol')
        symbol = r.get('trading_symbol', '')
        if (index not in EXCHANGES or r.get('segment') != 'FNO'
                or r.get('exchange') != EXCHANGES[index]
                or not SYMBOL.fullmatch(symbol)):
            continue
        expiry = clean_date(r.get('expiry_date'))
        if expiry is None:
            continue
        try:
            lot = int(r['lot_size']); tick = float(r['tick_size']); strike = float(r['strike_price'])
            if lot <= 0 or number(tick) <= 0 or number(strike) <= 0:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        catalog.setdefault((index, expiry), {})[symbol] = dict(symbol=symbol, index=index, expiry=expiry,
                                                              strike=strike, lot_size=lot, tick_size=tick)
    return catalog


def metadata(text, index, expiry):
    """Compatible single-expiry view for callers without a master cache."""
    return metadata_catalog(text).get((index, expiry), {})


class GrowwPreparation:
    def __init__(self, market, journal, *, clock=lambda:datetime.now(timezone.utc), master=download_instrument_text):
        self.market, self.journal, self.clock, self.master = market, journal, clock, master
        self.contracts = {}; self.master_key = None

    def call(self, method, **kwargs):
        self.market.limiter.wait()
        return method(timeout=5, **kwargs)

    def check(self, snapshot, cfg):
        now = self.clock()
        result = dict(status='WAIT', reason='FRESH_COMPLETE_DATA_REQUIRED', broker_writes=False,
                      at=now.isoformat(), selected=None, execution_enabled=False)
        if not p.entry_window(now):
            return dict(result, reason='OUTSIDE_1400_1900_JST')
        if (not snapshot or not fresh(snapshot['finished_at'], now)
                or snapshot.get('positions_status') != 'AVAILABLE' or snapshot.get('orders_status') != 'AVAILABLE'):
            return result
        executor = self.journal.store.meta('premium-executor-v1', {})
        occupied = self.journal.slot_status()['new_entry_blocked']
        owned = executor.get('phase') not in (None, 'CLOSED')
        day = now.astimezone(JST).date().isoformat()
        preferred = p.preferred_index(now)
        late_day = day in snapshot.get('expiry_evidence',{}).get(preferred,{}).get('expiries',[])
        roll = False
        forced = None
        if owned and executor.get('phase') in ('ENTRY_HEDGE','ENTRY_SHORT'):
            forced = executor['candidate']
        elif owned and executor.get('phase') in ('ROLL_CLOSE_SHORT','ROLL_REHEDGE'):
            forced = executor['roll_candidate']; roll = True
        elif owned and executor.get('phase') == 'MONITORING' and executor.get('strategy') == 'EVERYDAY':
            short_symbol = executor['candidate']['short']['symbol']
            option = next((r for r in snapshot.get('ordered_options', []) if r['symbol']==short_symbol), {})
            premium = (option.get('quote') or {}).get('offer_price')
            roll = not late_day and bool(executor.get('queued_roll') or (premium is not None and premium <= p.rollover_threshold(cfg,executor['candidate']['index'])))
        index = executor['candidate']['index'] if (forced or roll) else p.preferred_index(now)
        day = now.astimezone(JST).date().isoformat()
        evidence = snapshot.get('expiry_evidence', {}).get(index, {})
        if evidence.get('status') != 'CONFIRMED_CURRENT_MASTER' or evidence.get('day_jst') != day:
            return dict(result, reason='CURRENT_EXPIRY_REQUIRED')
        expiries = sorted(d for d in evidence.get('expiries', []) if d >= day)
        if not expiries:
            return dict(result, reason='CURRENT_EXPIRY_REQUIRED')
        expiry = executor['candidate']['expiry'] if (forced or roll) else expiries[0]
        strategy = 'LATE_SESSION' if day in expiries else 'EVERYDAY'
        trend = 'FLAT'; kind = 'CE'
        if strategy == 'LATE_SESSION':
            if now.astimezone(JST).hour < 18:
                return dict(result, reason='WAIT_FOR_ACTUAL_EXPIRY_1800_JST', strategy=strategy)
            trend = observed_trend(self.journal.store, index, now)
            kind = cfg['late_session']['trend_mapping'].get(trend)
            if kind not in ('CE', 'PE'):
                return dict(result, reason='VERIFIED_DIRECTIONAL_TREND_REQUIRED', strategy=strategy)
        if occupied and not owned:
            return dict(result, reason='ENGINE_SLOT_OCCUPIED_RECONCILE_BEFORE_ENTRY')
        chain = self.call(self.market.groww.get_option_chain, exchange=EXCHANGES[index], underlying=index, expiry_date=expiry)
        samples = []
        for strike, sides in chain.get('strikes', {}).items():
            call = sides.get(kind) if isinstance(sides, dict) else None
            try:
                if not call or not SYMBOL.fullmatch(call['trading_symbol']) or number(call['ltp']) <= 0:
                    continue
                samples.append((abs(call['ltp']-cfg['short_call_target_rupees'][index]), float(strike), call['trading_symbol']))
            except (ValueError, TypeError, KeyError):
                continue
        if not samples:
            return dict(result, reason='CURRENT_CALL_CHAIN_REQUIRED')
        samples.sort()
        if strategy == 'LATE_SESSION':
            proposed = p.expiry_short(cfg, index, chain.get('underlying_ltp'), [s[1] for s in samples], trend, actual_expiry=True)
            matches = [s for s in samples if s[1] == proposed.get('short_strike')]
            if not matches:
                return dict(result, reason='VERIFIED_LISTED_EXPIRY_STRIKES_REQUIRED', strategy=strategy)
            short_sample = matches[0]
            evidence = dict(evidence, spot=chain.get('underlying_ltp'))
        elif forced:
            matches = [s for s in samples if s[2] == forced['short']['symbol']]
            if not matches: return dict(result, reason='CURRENT_OWNED_SHORT_MASTER_REQUIRED')
            short_sample = matches[0]
        elif roll and not executor.get('queued_roll'):
            previous = executor['candidate']['short']
            # Use actual listed symbols/LTP only to select the bounded quote
            # sample; executable bid above the index threshold is checked below.
            eligible = [r for r in samples if r[1]<previous['strike'] and
                        next((number(sides[kind]['ltp']) for strike,sides in chain['strikes'].items()
                              if float(strike)==r[1] and kind in sides),0)>p.rollover_threshold(cfg,index)]
            if not eligible: return dict(result, reason='NEXT_LISTED_SHORT_ABOVE_THRESHOLD_REQUIRED')
            short_sample = max(eligible,key=lambda r:r[1])
        else:
            short_sample = samples[0]
        # Chain LTP identifies a sample; current bid/ask/quantity are read below.
        hedge_samples = [r for r in samples if (r[1] > short_sample[1] if kind=='CE' else r[1] < short_sample[1])]
        hedges = sorted(hedge_samples, key=lambda r:abs(r[1]-short_sample[1]))[:4]
        if forced:
            hedges = [r for r in hedge_samples if r[2]==forced['hedge']['symbol']]
        elif roll:
            old_hedge = executor['candidate']['hedge']['symbol']
            current = [r for r in hedge_samples if r[2]==old_hedge]
            hedges = current + [r for r in hedges if r[2]!=old_hedge]
        if roll and forced:
            old_hedge=executor['candidate']['hedge']['symbol']
            hedges += [r for r in hedge_samples if r[2]==old_hedge and r not in hedges]
        wanted = [short_sample]+hedges
        master_key = (index, expiry, int(now.timestamp())//3600)
        if master_key != self.master_key:
            self.contracts = metadata(self.master(), index, expiry); self.master_key = master_key
        active = [dict(symbol=r['symbol'], side=r['side'], quantity=r['quantity'], product='NRML',
                       ownership=r.get('ownership')) for r in snapshot.get('ordered_options', [])
                  if r.get('order_status') == 'POSITION']
        protected = {r['symbol'] for r in active if r['ownership'] != 'ENGINE_VERIFIED'}
        protected.update(r['symbol'] for r in self.journal.store.read('SELECT symbol FROM pc_protected'))
        quotes = []
        book_evidence = dict(sampled=len(wanted), protected=0, master_missing=0, invalid=0, valid=0)
        for _, _, symbol in wanted:
            if symbol in protected:
                book_evidence['protected'] += 1
                continue
            if symbol not in self.contracts:
                book_evidence['master_missing'] += 1
                continue
            raw = self.call(self.market.groww.get_quote, exchange=EXCHANGES[index], segment='FNO', trading_symbol=symbol)
            at = self.clock(); q = quote_summary(raw, at)
            try:
                quotes.append(leg(dict(self.contracts[symbol], bid=q['bid_price'], ask=q['offer_price'],
                    bid_quantity=q['bid_quantity'], ask_quantity=q['offer_quantity'], received_at=at.isoformat())))
                book_evidence['valid'] += 1
            except (ValueError, TypeError):
                book_evidence['invalid'] += 1
                continue
        result['book_evidence'] = book_evidence
        if not any(r['symbol'] == short_sample[2] for r in quotes):
            reason = ('TARGET_SHORT_MANUAL_PROTECTED' if short_sample[2] in protected else
                      'TARGET_SHORT_MASTER_REQUIRED' if short_sample[2] not in self.contracts else
                      'TARGET_SHORT_BOOK_REQUIRED')
            return dict(result, reason=reason)
        short = next(r for r in quotes if r['symbol'] == short_sample[2]); margins = {}
        def order(q, side, qty):
            return dict(trading_symbol=q['symbol'], exchange=EXCHANGES[index], product='NRML',
                        transaction_type=side, quantity=qty, order_type='LIMIT', price=q['ask' if side=='BUY' else 'bid'])
        calculated = {}
        for hedge in (q for q in quotes if (q['strike'] > short['strike'] if kind=='CE' else q['strike'] < short['strike'])):
            lots_values = [forced['lots']] if forced else [executor['short_quantity']//short['lot_size']] if roll else range(1, (cfg['maximum_lots'] or 0)+1)
            for lots in lots_values:
                qty = lots*short['lot_size']
                if short['bid_quantity'] < qty or hedge['ask_quantity'] < qty:
                    continue
                try:
                    opening = [order(hedge, 'BUY', qty), order(short, 'SELL', qty)]
                    held_hedge = any(r.get('symbol')==hedge['symbol'] and r.get('side')=='BUY'
                        and r.get('quantity')>=qty and r.get('ownership')=='ENGINE_VERIFIED'
                        for r in snapshot.get('ordered_options',[]))
                    if held_hedge: opening = opening[1:]
                    entry = self.call(self.market.groww.get_order_margin_details, segment='FNO', orders=opening)
                    hedge_only = self.call(self.market.groww.get_order_margin_details, segment='FNO', orders=[order(hedge,'BUY',qty)]) if not held_hedge else {'total_requirement':0}
                    closing = [order(short, 'BUY', qty), order(hedge, 'SELL', qty)]
                    exit_quote = self.call(self.market.groww.get_order_margin_details, segment='FNO', orders=closing)
                    margins[basket_key(short, hedge, qty)] = dict(received_at=self.clock().isoformat(),
                        basket_requirement_inr=number(entry['total_requirement']),
                        hedge_requirement_inr=number(hedge_only['total_requirement']),
                        round_trip_charges_inr=number(entry['brokerage_and_charges'])+number(exit_quote['brokerage_and_charges']))
                    calculated[basket_key(short,hedge,qty)] = dict(held_hedge=held_hedge,opening_cost=number(entry['brokerage_and_charges']))
                except Exception:
                    continue  # Failed calculation is unknown, never zero cost/margin.
        raw = self.call(self.market.groww.get_available_margin_details)
        at = self.clock(); funds = dict(funds_summary(raw), received_at=at.isoformat())
        result.update(at=at.isoformat(), calculation_evidence=dict(available=len(margins)),
                      candidate_scope='CHAIN_SAMPLE_ONLY',
                      omitted_hedges=max(0, len(hedge_samples)-len(hedges)),
                      globally_maximum_profit_verified=False)
        # A slow sequence of analytical requests can outlive its original books.
        # It proves neither affordability nor an executable price. Keep the same
        # freshness gate and expose the actual failure instead of claiming that
        # zero evaluated candidates were all unaffordable.
        book_evidence['stale'] = sum(not fresh(q['received_at'], at) for q in quotes)
        if not fresh(short['received_at'], at):
            return dict(result, reason='SHORT_BOOK_EXPIRED_DURING_CALCULATIONS')
        if not any(q['symbol'] != short['symbol'] and fresh(q['received_at'], at) for q in quotes):
            return dict(result, reason='FRESH_EXCLUSIVE_HEDGE_BOOK_REQUIRED')
        # Expiry mapping needs the complete listed strike grid, while quotes are
        # deliberately bounded. Rank only the mapped short and sampled hedges.
        if strategy == 'LATE_SESSION':
            prepared = self._expiry_rank(cfg, quotes, margins, funds, evidence, at, active, trend, samples)
        else:
            prepared = rank_baskets(cfg, quotes, margins, funds, evidence, at, active=active, complete=True)
        if forced or roll:
            # Existing hedge/margin is already in the broker's actual state. Do
            # not charge its purchase twice or increase size during a short roll.
            candidates=[]
            old_hedge=executor['candidate']['hedge']
            old_book=next((q for q in quotes if q['symbol']==old_hedge['symbol']),None)
            for hedge in quotes:
                if hedge['symbol']==short['symbol']: continue
                if forced and hedge['symbol']!=forced['hedge']['symbol']: continue
                lots=forced['lots'] if forced else executor['short_quantity']//short['lot_size']; qty=lots*short['lot_size']
                k=basket_key(short,hedge,qty); m=margins.get(k); info=calculated.get(k)
                if not m or not info or not fresh(m['received_at'],at): continue
                if short['bid_quantity']<qty or hedge['ask_quantity']<qty: continue
                if m['basket_requirement_inr']>funds['option_sell_available_inr']: continue
                if not info['held_hedge'] and max(m['hedge_requirement_inr'],hedge['ask']*qty)>funds['option_buy_available_inr']: continue
                improvement=0; incremental=m['round_trip_charges_inr']
                if roll and not info['held_hedge']:
                    if not old_book: continue
                    try:
                        replacement = self.call(self.market.groww.get_order_margin_details, segment='FNO',
                            orders=[order(hedge,'BUY',qty),order(old_book,'SELL',qty)])
                        incremental += number(replacement['brokerage_and_charges'])
                    except Exception: continue
                    improvement=(old_book['bid']-hedge['ask'])*qty-number(replacement['brokerage_and_charges'])
                    if improvement<=100: continue
                credit=short['bid']-hedge['ask']; width=abs(short['strike']-hedge['strike'])
                if not 0<credit<width: continue
                if roll and not executor.get('queued_roll') and short['bid']<=p.rollover_threshold(cfg,index): continue
                candidates.append(dict(key=k,index=index,expiry=expiry,strategy=executor['strategy'],short=short,hedge=hedge,
                    quantity=qty,lots=lots,product='NRML',net_max_expiry_profit_inr=credit*qty-m['round_trip_charges_inr'],
                    basket_requirement_inr=m['basket_requirement_inr'],round_trip_charges_inr=m['round_trip_charges_inr'],
                    incremental_costs_inr=incremental,held_hedge=info['held_hedge'],hedge_improvement_after_costs_inr=improvement,
                    prepared_at=self.clock().isoformat(),broker_writes=False))
            if candidates:
                candidates.sort(key=lambda r:(-r['net_max_expiry_profit_inr'],r['basket_requirement_inr']))
                prepared.update(status='PREPARED_ROLL' if roll else 'PREPARED_OWNED_ENTRY',selected=candidates[0],reason='CURRENT_OWNED_ACTION_CALCULATED')
            else: prepared.update(status='WAIT',selected=None,reason='OWNED_ACTION_MARGIN_AND_BOOKS_REQUIRED')
        prepared.update(at=at.isoformat(), execution_enabled=False, candidate_scope='CHAIN_SAMPLE_ONLY',
                        omitted_hedges=max(0, len(hedge_samples)-len(hedges)),
                        globally_maximum_profit_verified=False, book_evidence=book_evidence,
                        calculation_evidence=result['calculation_evidence'])
        if prepared.get('selected'):
            prepared['selected']['expiry_evidence']=evidence
            if strategy=='LATE_SESSION':
                prepared['selected'].update(trend=trend,spot=chain.get('underlying_ltp'),listed_strikes=[s[1] for s in samples])
        return prepared

    @staticmethod
    def _expiry_rank(cfg, quotes, margins, funds, evidence, now, active, trend, samples):
        # Preserve the full grid in evidence; rank_baskets must not derive ATM
        # from the small quote sample.
        evidence = dict(evidence, listed_strikes=[s[1] for s in samples])
        return rank_baskets(cfg, quotes, margins, funds, evidence, now, strategy='LATE_SESSION',
                            trend=trend, active=active, complete=True)

    def safe_check(self, snapshot, cfg):
        if getattr(self,'entries_retired',False):
            result=dict(status='WAIT',reason='RETIRED_STRATEGY_NEW_ENTRY_DISABLED',
                at=self.clock().isoformat(),broker_writes=False,selected=None,execution_enabled=False)
            self.journal.store.set_meta('premium-preparation',result)
            return result
        try:
            result = self.check(snapshot, cfg)
        except Exception as exc:
            result = dict(status='WAIT', reason='BROKER_PREPARATION_UNAVAILABLE', error=safe_error(exc),
                          at=self.clock().isoformat(), broker_writes=False, selected=None, execution_enabled=False)
        self.journal.store.set_meta('premium-preparation', result)
        return result

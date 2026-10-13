"""Explicit protected desktop actions for the fixed Oracle owner setup CLI.

No polling path calls this controller. A lost response retains the original plan
and write attempt; only an explicit status/capture reads it back after restart.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from .contracts import dumps, identity, stamp
from .oracle_link import settings

KEY = 'desktop-owner-setup-v1'
ACTIONS = {'lookup','buy-protect','preview', 'status', 'capture', 'submit', 'close', 'cancel', 'protect', 'cancel-stop', 'retire', 'arm-preview', 'arm'}
WRITES = {'buy-protect','submit', 'close', 'cancel', 'protect', 'cancel-stop', 'arm'}
TERMINAL = {'COMPLETE', 'CANCELLED_FLAT', 'ABORTED_BEFORE_WRITE','CLOSED_UNVERIFIED'}
PREVIEW = 'OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT'
REMOTE_STATUSES = TERMINAL | {PREVIEW,'OWNER_CONTRACT_FOUND','OWNER_TEST_RUNNING','BUY_SUBMITTING','BUY_ACCEPTED','BUY_PENDING_OR_PARTIAL','BUY_RESULT_UNKNOWN', 'OWNER_SETUP_BLOCKED', 'GTT_SUBMITTING', 'GTT_ACCEPTED',
    'GTT_CREATE_UNCERTAIN', 'ACTIVE_PARENT_OBSERVED', 'CHILD_FILLED', 'CHILD_PENDING_OR_PARTIAL',
    'FLAT_AWAITING_SECOND_READ', 'RECONCILIATION_REQUIRED', 'CLOSE_SUBMITTING',
    'CLOSE_PENDING_OR_PARTIAL', 'CHILD_TERMINAL_WITH_REMAINDER',
    'VERIFIED_OWNER_EVIDENCE', 'LIVE_PREPARED_ALGO_OFF', 'OWNER_PREVIEW_EXPIRED',
    'OWNER_PREVIEW_BINDING_CHANGED', 'OWNER_PREVIEW_INVALID', 'PRIOR_TEST_RETIRED',
    'STOP_SUBMITTING', 'STOP_CREATE_UNCERTAIN', 'STOP_CANCEL_UNCERTAIN', 'STOP_ACTIVE_OBSERVED', 'STOP_CHILD_OBSERVED'}
FACTS = {'buy_order_link','oracle_order_write', 'persistent_gtt', 'exact_child_link', 'child_filled',
    'standard_order_write', 'standard_order_filled', 'flat_confirmed', 'inactive_parent',
    'stop_order_write', 'stop_persisted', 'stop_inactive'}
UNCERTAIN = {'BUY_SUBMITTING','BUY_RESULT_UNKNOWN','GTT_SUBMITTING', 'GTT_CREATE_UNCERTAIN', 'CLOSE_SUBMITTING', 'RECONCILIATION_REQUIRED',
    'STOP_SUBMITTING', 'STOP_CREATE_UNCERTAIN', 'STOP_CANCEL_UNCERTAIN'}
_LOCKS, _LOCKS_LOCK = {}, threading.Lock()


class SetupValidationError(ValueError):
    """Code-owned, non-secret validation reasons for the desktop form."""


def _rejected_preview(value):
    return value.get('status') == 'OWNER_PREVIEW_EXPIRED' or (
        value.get('status') == 'OWNER_SETUP_BLOCKED' and value.get('reason') in {
            'PREVIEW_PLAN_FIRST', 'COMMISSIONING_CURRENT_MASTER_MISMATCH',
            'COMMISSIONING_RECENT_OPTION_QUOTE_REQUIRED',
            'COMMISSIONING_TRIGGER_MUST_START_ABOVE_MARKET',
            'COMMISSIONING_INSUFFICIENT_PURCHASE_BALANCE',
            'COMMISSIONING_PLAN_EXPIRED_OR_TOO_LONG',
            'TURN_ALGO_OFF_BEFORE_SETUP',
            'FRESH_COMMISSIONING_OBSERVATION_REQUIRED'})


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{'+str(length)+'}', value) is not None


def _spec(value, now):
    required = {'contract', 'quantity', 'trigger_price', 'buy_limit_price', 'sell_limit_price', 'valid_until', 'egress_ip',
                'stop_trigger_price', 'stop_limit_price'}
    direct=isinstance(value,dict) and value.get('entry_mode')=='LIMIT'
    if direct:required.add('entry_mode')
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError()
    c = value['contract']
    if (not isinstance(c, dict) or set(c) != {'symbol', 'index', 'expiry', 'lot_size', 'tick_size'}
            or c['index'] not in ('NIFTY', 'SENSEX') or not isinstance(c['symbol'], str)
            or not re.fullmatch('[A-Z0-9]{8,50}(?:CE|PE)', c['symbol']) or not c['symbol'].startswith(c['index'])
            or type(c['lot_size']) is not int or not 1 <= c['lot_size'] <= 100000
            or type(value['quantity']) is not int or value['quantity'] != c['lot_size']):
        raise ValueError()
    date.fromisoformat(c['expiry'])
    if not 0 < (stamp(value['valid_until']) - now).total_seconds() <= 86400:
        raise SetupValidationError('COMMISSIONING_PLAN_EXPIRED_OR_TOO_LONG')
    address = ipaddress.ip_address(value['egress_ip'])
    if address.version != 4 or not address.is_global:
        raise ValueError()
    def price(v):
        if isinstance(v, bool) or not isinstance(v, (str, int, float)):
            raise ValueError()
        n = Decimal(str(v))
        if not n.is_finite() or not 0 < n < 1000000: raise ValueError()
        return n
    tick = price(c['tick_size'])
    prices = {k:price(value[k]) for k in ('trigger_price', 'buy_limit_price', 'sell_limit_price', 'stop_trigger_price', 'stop_limit_price')}
    if any(p % tick for p in prices.values()):
        raise SetupValidationError('COMMISSIONING_PRICE_NOT_ON_TICK')
    if prices['buy_limit_price'] < prices['trigger_price']:
        raise SetupValidationError('COMMISSIONING_BUY_LIMIT_BELOW_TRIGGER')
    if not prices['stop_limit_price'] <= prices['stop_trigger_price'] < prices['trigger_price']:
        raise SetupValidationError('COMMISSIONING_STOP_PRICE_ORDER_INVALID')
    result = copy.deepcopy(value)
    result['contract']['tick_size'] = float(tick)
    result.update({k:format(v, 'f') for k,v in prices.items()})
    if len(dumps(result)) > 8192: raise ValueError()
    return result


def _safe_response(raw):
    """Whitelist code statuses and public evidence; discard broker/private fields."""
    if not isinstance(raw, dict) or raw.get('status') not in REMOTE_STATUSES:
        raise ValueError()
    result = {'status':raw['status']}
    if raw['status']=='OWNER_CONTRACT_FOUND':
        c=raw['contract'];q=raw['quote']
        if (not isinstance(c,dict) or set(c)!={'symbol','index','expiry','lot_size','tick_size'} or
                c['index'] not in ('NIFTY','SENSEX') or not re.fullmatch('[A-Z0-9]{8,50}(?:CE|PE)',c['symbol']) or
                type(c['lot_size']) is not int or not 0<c['lot_size']<100000):raise ValueError()
        date.fromisoformat(c['expiry']);ipaddress.ip_address(raw['egress_ip'])
        if not 0<float(c['tick_size'])<100:raise ValueError()
        result.update(contract=copy.deepcopy(c),egress_ip=raw['egress_ip'],quote={
            k:v for k,v in q.items() if k in ('last_price','bid_price','offer_price','received_at','last_trade_time','timestamp_status')})
    reason = raw.get('reason')
    if reason is not None:
        result['reason'] = reason if isinstance(reason,str) and re.fullmatch(
            '(?:COMMISSIONING|OWNER|EXACT|CURRENT|FRESH|TURN|PAPER|DEPLOYED|EVIDENCE|ORIGINAL|LATEST|'
            'DIRECT|PUBLIC|PROTECTED|REVIEWED|IMMUTABLE|DECLARED|PREVIEW|SETUP|ROOT|INVALID|BOUNDED)_[A-Z0-9_]{1,100}',reason
        ) else 'OWNER_SETUP_REMOTE_BLOCKED'
    for key in ('broker_writes', 'broker_write_attempted', 'production_activation_changed', 'algo_enabled'):
        if raw.get(key) is None or type(raw.get(key)) is bool:
            if key in raw: result[key] = raw[key]
        else: raise ValueError()
    if raw.get('broker_write_outcome') in ('ACCEPTED', 'UNCERTAIN', 'NOT_SUBMITTED'):
        result['broker_write_outcome'] = raw['broker_write_outcome']
    if raw.get('command') in WRITES: result['command'] = raw['command']
    if 'allowed_actions' in raw:
        actions=raw['allowed_actions']
        if not isinstance(actions,list) or any(a not in ('close','cancel','protect','cancel-stop') for a in actions): raise ValueError()
        result['broker_actions']=list(dict.fromkeys(actions))
    for key in ('checked_at', 'completed_at'):
        if raw.get(key) is not None: result[key] = stamp(raw[key]).isoformat()
    if 'facts' in raw:
        if not isinstance(raw['facts'],dict) or any(type(v) is not bool for k,v in raw['facts'].items() if k in FACTS):
            raise ValueError()
        result['facts'] = {k:v for k,v in raw['facts'].items() if k in FACTS}
    for key in ('plan_hash', 'evidence_digest'):
        if key in raw:
            if not _hex(raw[key],64): raise ValueError()
            result[key] = raw[key]
    return result


class OwnerSetupController:
    def __init__(self, root, store, *, run=subprocess.run, clock=lambda:datetime.now(timezone.utc)):
        self.root, self.store, self.run, self.clock = Path(root), store, run, clock
        with _LOCKS_LOCK:
            self.lock = _LOCKS.setdefault(str(self.root.resolve()),threading.Lock())
        self.activity_lock = threading.Lock()
        self.activity = None

    def _config(self):
        source = self.root/'.agent-state/oracle-viewer.json'
        if source.stat().st_size > 8192: raise ValueError()
        return settings(json.loads(source.read_text(encoding='utf-8-sig')))

    def _save(self, saved):
        self.store.set_meta(KEY,saved)

    def _remote(self, cfg, action, saved, confirm=None):
        command = 'sudo -n '+cfg['python']+' -I -m nifty_engine.agent_engine.owner_setup '
        command += ({'preview':'preview-stdin','lookup':'lookup-stdin'}.get(action,action))+' --id '+saved['plan_id']
        if confirm is not None: command += ' --confirm '+confirm
        ssh = Path(os.environ.get('SYSTEMROOT','C:/Windows'))/'System32/OpenSSH/ssh.exe'
        args = [str(ssh),'-F','NUL','-T','-i',cfg['identity_file'],'-o','BatchMode=yes',
            '-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','IdentitiesOnly=yes',
            '-o','IdentityAgent=none','-o','PasswordAuthentication=no','-o','KbdInteractiveAuthentication=no',
            '-o','ClearAllForwardings=yes','-o','ConnectTimeout=8','-o','LogLevel=ERROR',
            cfg['user']+'@'+cfg['host'],command]
        response = self.run(args,input=dumps(saved['spec']) if action in ('preview','lookup') else '',
            capture_output=True,text=True,encoding='utf-8',timeout=1600 if action == 'arm' else 210,
            **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        if not isinstance(response.stdout,str) or len(response.stdout.encode('utf-8')) > 32768:
            raise ValueError()
        raw = json.loads(response.stdout)
        safe = _safe_response(raw)
        if response.returncode and safe['status'] != 'OWNER_SETUP_BLOCKED': raise ValueError()
        if raw.get('plan_id') is not None and raw['plan_id'] != saved['plan_id']: raise ValueError()
        if action == 'preview' and safe['status'] == PREVIEW:
            if raw.get('plan_id') != saved['plan_id'] or not safe.get('plan_hash'): raise ValueError()
        old_hash = saved.get('plan_hash')
        if old_hash and safe.get('plan_hash',old_hash) != old_hash: raise ValueError()
        return safe

    def _view(self, saved=None, **override):
        saved = saved if isinstance(saved,dict) else self.store.meta(KEY,{})
        value = copy.deepcopy(saved.get('result',{}))
        value.setdefault('status','NOT_STARTED')
        value.update(format='trading-desktop-owner-setup-v1',plan_hash=saved.get('plan_hash'),
            plan_spec=copy.deepcopy(saved.get('spec')),pending_action=saved.get('pending_action'),
            requires_reconciliation=bool(saved.get('uncertain') or saved.get('pending_action')),
            evidence_digest=saved.get('evidence_digest'),
            allowed_actions=['status'])
        if saved.get('plan_id'):
            value['allowed_actions'].append('capture')
            value['allowed_actions'].append('retire')
            if not saved.get('uncertain') and not saved.get('pending_action'):
                attempts=saved.get('attempted_writes',[])
                recent=saved.get('writes_since_capture',[])
                unsubmitted_rejected=_rejected_preview(value)
                if (value['status'] in TERMINAL or value['status']==PREVIEW and not attempts
                        or unsubmitted_rejected and not attempts):
                    value['allowed_actions'].append('preview')
                buy_action='buy-protect' if saved.get('spec',{}).get('entry_mode')=='LIMIT' else 'submit'
                if value['status']==PREVIEW and saved.get('plan_hash') and buy_action not in attempts:
                    value['allowed_actions'].append(buy_action)
                if saved.get('plan_hash'):
                    for action in ('close','cancel','protect','cancel-stop'):
                        if action in value.get('broker_actions',[]) and action not in recent:
                            value['allowed_actions'].append(action)
                if value['status'] in ('COMPLETE','VERIFIED_OWNER_EVIDENCE'):
                    value['allowed_actions'].append('arm-preview')
                if saved.get('evidence_digest') and 'arm' not in attempts:
                    value['allowed_actions'].append('arm')
        else: value['allowed_actions'].append('preview')
        value.update(override)
        return value

    def public(self):
        """Local display only; this never issues an Oracle or broker command."""
        try:
            value=self._view()
            if not value.get('plan_spec'):
                drafts=[json.loads(r['body']).get('spec') for r in self.store.read(
                    'SELECT body FROM meta WHERE key LIKE ?', (KEY+'-archive-%',))]
                drafts=[d for d in drafts if isinstance(d,dict) and d.get('valid_until')]
                value['draft_spec']=max(drafts,key=lambda d:stamp(d['valid_until'])) if drafts else None
            value['status_source']='SAVED_LOCAL_STATE'
            with self.activity_lock:
                activity=copy.deepcopy(self.activity)
            value['operation']=dict(action=activity['action'],
                elapsed_seconds=round(max(0,time.monotonic()-activity['started']),1)) if activity else None
            return value
        except Exception: return {'status':'OWNER_SETUP_LOCAL_STATE_UNAVAILABLE','allowed_actions':['status']}

    def command(self, body):
        if not self.lock.acquire(blocking=False):
            return {'status':'OWNER_SETUP_BUSY','allowed_actions':['status']}
        try:
            if not isinstance(body,dict) or body.get('action') not in ACTIONS: raise ValueError()
            action=body['action']
            expected={'action','spec'} if action in ('preview','lookup') else {'action','confirm'} if action in WRITES else {'action'}
            if set(body)!=expected: raise ValueError()
            with self.activity_lock:
                self.activity={'action':action,'started':time.monotonic()}
            try: cfg=self._config()
            except OSError:
                return self._view(status='ORACLE_CONNECTION_REQUIRED',reason='PRIVATE_ORACLE_CONNECTION_CONFIG_REQUIRED')
            saved=self.store.meta(KEY,{})
            if action=='lookup':
                spec=body['spec']
                if (not isinstance(spec,dict) or set(spec)!={'index','symbol'} or spec['index'] not in ('NIFTY','SENSEX') or
                        not isinstance(spec['symbol'],str) or not re.fullmatch('[A-Z0-9]{8,50}(?:CE|PE)',spec['symbol'])):raise ValueError()
                return self._remote(cfg,'lookup',dict(plan_id=uuid.uuid4().hex,spec=spec))
            if saved and saved.get('config_hash') != identity(cfg) and action != 'retire':
                if action=='preview' and not saved.get('attempted_writes') and saved.get('pending_action') in (None,'preview'):
                    # Explicit Review may archive an unused preview only after
                    # Oracle proves there is no submitted/authorized test.
                    remote=self._remote(cfg,'retire',saved)
                    if remote['status']!='PRIOR_TEST_RETIRED':
                        return self._view(saved,status=remote['status'],reason=remote.get('reason'))
                    self.store.set_meta(KEY+'-archive-'+saved['plan_id'],dict(saved,retirement=remote))
                    saved={};self._save(saved)
                else:
                    return self._view(saved,status='OWNER_SETUP_BINDING_CHANGED',reason='REVIEW_EXISTING_PLAN_ON_ORACLE')
            if action == 'retire' and saved:
                remote = self._remote(cfg, 'retire', saved)
                if remote['status'] == 'PRIOR_TEST_RETIRED':
                    self.store.set_meta(KEY + '-archive-' + saved['plan_id'], dict(saved, retirement=remote))
                    self._save({})
                    return self._view({}, status='PRIOR_TEST_RETIRED')
                return self._view(saved, status=remote['status'], reason=remote.get('reason'))
            if action=='preview':
                spec=_spec(body['spec'],self.clock())
                if saved:
                    prior_pending=bool(saved.get('pending_action') or saved.get('uncertain'))
                    remote=self._remote(cfg,'status',saved)
                    self._accept(saved,'status',remote)
                    replaceable=remote['status'] in TERMINAL | {PREVIEW}
                    rejected_preview=_rejected_preview(remote)
                    if rejected_preview and not saved.get('attempted_writes'): replaceable=True
                    if prior_pending or not replaceable:
                        return self._view(saved,reason='EXISTING_PLAN_REQUIRES_REVIEW')
                    if remote['status']==PREVIEW and (saved.get('attempted_writes') or saved.get('spec')==spec):
                        return self._view(saved)
                saved=dict(format=KEY,plan_id=uuid.uuid4().hex,config_hash=identity(cfg),spec=spec,
                    plan_hash=None,evidence_digest=None,attempted_writes=[],writes_since_capture=[],
                    result={'status':'PREVIEW_PENDING'})
            elif not saved:
                return self._view(saved)
            if not _hex(saved.get('plan_id'),32): raise ValueError()
            if action in WRITES:
                expected_hash=saved.get('evidence_digest') if action=='arm' else saved.get('plan_hash')
                if not _hex(body['confirm'],64) or body['confirm']!=expected_hash:
                    return self._view(saved,status='OWNER_EXACT_CONFIRMATION_REQUIRED')
                if action not in self._view(saved)['allowed_actions']:
                    return self._view(saved,reason='READ_CURRENT_PLAN_BEFORE_ANOTHER_WRITE')
                saved['attempted_writes'].append(action)
                saved.setdefault('writes_since_capture',[]).append(action)
            prior_unknown_write=saved.get('pending_action') in WRITES
            if not prior_unknown_write or action not in ('status','capture'):
                saved['pending_action']=action
            self._save(saved)  # Durable before SSH; never hold a SQL transaction over network I/O.
            try:
                remote=self._remote(cfg,action,saved,body.get('confirm'))
            except Exception:
                saved['uncertain']=True
                saved['result']={'status':'OWNER_SETUP_RESULT_UNKNOWN','reason':'READ_ORIGINAL_PLAN_STATUS',
                    'broker_writes':None if action in WRITES or prior_unknown_write else False}
                self._save(saved)
                return self._view(saved)
            self._accept(saved,action,remote)
            return self._view(saved)
        except SetupValidationError as exc:
            return self._view(status='INVALID_OWNER_SETUP_COMMAND',reason=str(exc))
        except Exception:
            try:
                return self._view(status='INVALID_OWNER_SETUP_COMMAND',reason='CHECK_SETUP_COMMAND_FIELDS')
            except Exception:
                return {'status':'OWNER_SETUP_LOCAL_STATE_UNAVAILABLE','allowed_actions':['status']}
        finally:
            with self.activity_lock:
                self.activity=None
            self.lock.release()

    def _accept(self,saved,action,remote):
        saved['result']=remote
        if remote.get('plan_hash'): saved['plan_hash']=remote['plan_hash']
        # An arm preview applies only to this exact saved plan and current read.
        if action=='arm-preview' and remote['status']=='VERIFIED_OWNER_EVIDENCE' and remote.get('evidence_digest'):
            saved['evidence_digest']=remote['evidence_digest']
        elif action not in ('status','arm'): saved['evidence_digest']=None
        unresolved=remote['status'] in UNCERTAIN or remote.get('broker_write_outcome')=='UNCERTAIN'
        if remote['status']=='OWNER_SETUP_BLOCKED' and saved.get('pending_action') in WRITES:
            unresolved=True  # A remote exception is not proof no request reached the broker.
        if saved.get('pending_action')=='arm' and action!='arm' and remote['status']!='LIVE_PREPARED_ALGO_OFF':
            unresolved=True  # A complete test record alone does not confirm the live-mode transition.
        saved['uncertain']=unresolved
        if not unresolved: saved['pending_action']=None
        if action=='capture' and not unresolved and remote['status']!='OWNER_SETUP_BLOCKED':
            saved['writes_since_capture']=[]
        self._save(saved)

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
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from .contracts import dumps, identity, stamp
from .oracle_link import settings

KEY = 'desktop-owner-setup-v1'
ACTIONS = {'preview', 'status', 'capture', 'submit', 'close', 'cancel', 'arm-preview', 'arm'}
WRITES = {'submit', 'close', 'cancel', 'arm'}
TERMINAL = {'COMPLETE', 'CANCELLED_FLAT', 'ABORTED_BEFORE_WRITE'}
PREVIEW = 'OWNER_PLAN_READY_FOR_EXPLICIT_SUBMIT'
REMOTE_STATUSES = TERMINAL | {PREVIEW, 'OWNER_SETUP_BLOCKED', 'GTT_SUBMITTING', 'GTT_ACCEPTED',
    'GTT_CREATE_UNCERTAIN', 'ACTIVE_PARENT_OBSERVED', 'CHILD_FILLED', 'CHILD_PENDING_OR_PARTIAL',
    'FLAT_AWAITING_SECOND_READ', 'RECONCILIATION_REQUIRED', 'CLOSE_SUBMITTING',
    'CLOSE_PENDING_OR_PARTIAL', 'CHILD_TERMINAL_WITH_REMAINDER',
    'VERIFIED_OWNER_EVIDENCE', 'LIVE_PREPARED_ALGO_OFF', 'OWNER_PREVIEW_EXPIRED',
    'OWNER_PREVIEW_BINDING_CHANGED', 'OWNER_PREVIEW_INVALID'}
FACTS = {'oracle_order_write', 'persistent_gtt', 'exact_child_link', 'child_filled',
    'standard_order_write', 'standard_order_filled', 'flat_confirmed', 'inactive_parent'}
UNCERTAIN = {'GTT_SUBMITTING', 'GTT_CREATE_UNCERTAIN', 'CLOSE_SUBMITTING', 'RECONCILIATION_REQUIRED'}
_LOCKS, _LOCKS_LOCK = {}, threading.Lock()


def _hex(value, length):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{'+str(length)+'}', value) is not None


def _spec(value, now):
    required = {'contract', 'quantity', 'trigger_price', 'buy_limit_price', 'sell_limit_price', 'valid_until', 'egress_ip'}
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
        raise ValueError()
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
    prices = {k:price(value[k]) for k in ('trigger_price', 'buy_limit_price', 'sell_limit_price')}
    if (any(p % tick for p in prices.values()) or prices['buy_limit_price'] < prices['trigger_price']
            or prices['buy_limit_price'] * value['quantity'] > 1000):
        raise ValueError()
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
        if not isinstance(actions,list) or any(a not in ('close','cancel') for a in actions): raise ValueError()
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

    def _config(self):
        source = self.root/'.agent-state/oracle-viewer.json'
        if source.stat().st_size > 8192: raise ValueError()
        return settings(json.loads(source.read_text(encoding='utf-8-sig')))

    def _save(self, saved):
        self.store.set_meta(KEY,saved)

    def _remote(self, cfg, action, saved, confirm=None):
        command = 'sudo -n '+cfg['python']+' -I -m nifty_engine.agent_engine.owner_setup '
        command += ('preview-stdin' if action == 'preview' else action)+' --id '+saved['plan_id']
        if confirm is not None: command += ' --confirm '+confirm
        ssh = Path(os.environ.get('SYSTEMROOT','C:/Windows'))/'System32/OpenSSH/ssh.exe'
        args = [str(ssh),'-F','NUL','-T','-i',cfg['identity_file'],'-o','BatchMode=yes',
            '-o','StrictHostKeyChecking=yes','-o','UpdateHostKeys=no','-o','IdentitiesOnly=yes',
            '-o','IdentityAgent=none','-o','PasswordAuthentication=no','-o','KbdInteractiveAuthentication=no',
            '-o','ClearAllForwardings=yes','-o','ConnectTimeout=8','-o','LogLevel=ERROR',
            cfg['user']+'@'+cfg['host'],command]
        response = self.run(args,input=dumps(saved['spec']) if action == 'preview' else '',
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
            if not saved.get('uncertain') and not saved.get('pending_action'):
                attempts=saved.get('attempted_writes',[])
                recent=saved.get('writes_since_capture',[])
                unsubmitted_rejected=(value['status']=='OWNER_PREVIEW_EXPIRED' or
                    value['status']=='OWNER_SETUP_BLOCKED' and value.get('reason')=='PREVIEW_PLAN_FIRST')
                if (value['status'] in TERMINAL or value['status']==PREVIEW and not attempts
                        or unsubmitted_rejected and not attempts):
                    value['allowed_actions'].append('preview')
                if value['status']==PREVIEW and saved.get('plan_hash') and 'submit' not in attempts:
                    value['allowed_actions'].append('submit')
                if saved.get('plan_hash'):
                    for action in ('close','cancel'):
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
        try: return self._view()
        except Exception: return {'status':'OWNER_SETUP_LOCAL_STATE_UNAVAILABLE','allowed_actions':['status']}

    def command(self, body):
        if not self.lock.acquire(blocking=False):
            return {'status':'OWNER_SETUP_BUSY','allowed_actions':['status']}
        try:
            if not isinstance(body,dict) or body.get('action') not in ACTIONS: raise ValueError()
            action=body['action']
            expected={'action','spec'} if action=='preview' else {'action','confirm'} if action in WRITES else {'action'}
            if set(body)!=expected: raise ValueError()
            try: cfg=self._config()
            except OSError:
                return self._view(status='ORACLE_CONNECTION_REQUIRED',reason='PRIVATE_ORACLE_CONNECTION_CONFIG_REQUIRED')
            saved=self.store.meta(KEY,{})
            if saved and saved.get('config_hash') != identity(cfg):
                return self._view(saved,status='OWNER_SETUP_BINDING_CHANGED',reason='REVIEW_EXISTING_PLAN_ON_ORACLE')
            if action=='preview':
                spec=_spec(body['spec'],self.clock())
                if saved:
                    prior_pending=bool(saved.get('pending_action') or saved.get('uncertain'))
                    remote=self._remote(cfg,'status',saved)
                    self._accept(saved,'status',remote)
                    replaceable=remote['status'] in TERMINAL | {PREVIEW}
                    rejected_preview=(remote['status']=='OWNER_PREVIEW_EXPIRED' or
                        remote['status']=='OWNER_SETUP_BLOCKED' and remote.get('reason')=='PREVIEW_PLAN_FIRST')
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
        except Exception:
            try:
                return self._view(status='INVALID_OWNER_SETUP_COMMAND',reason='CHECK_SETUP_COMMAND_FIELDS')
            except Exception:
                return {'status':'OWNER_SETUP_LOCAL_STATE_UNAVAILABLE','allowed_actions':['status']}
        finally:
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

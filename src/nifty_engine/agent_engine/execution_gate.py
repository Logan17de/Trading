"""Oracle activation gate and one-request, thread-local broker write capability.

The desktop changes owner intent only. An operator-reviewed activation record,
LIVE environment, exact immutable release and removed pause are separate gates.
No maintenance path creates that record or enables LIVE.
"""
from __future__ import annotations

import contextlib
import contextvars
import re
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import identity
from .execution import fresh
from . import premium_strategy as policy

_grant = contextvars.ContextVar('oracle_broker_write', default=None)


class ExecutionDenied(PermissionError):
    """Raised before network I/O; an aborted intent can safely be re-prepared."""


class ExecutionGate:
    def __init__(self, journal, pause_file, *, mode, release, host, clock, policy_hash, entries_retired=False):
        self.journal, self.pause_file = journal, Path(pause_file)
        self.mode, self.release, self.host = mode, release, host
        self.clock, self.policy_hash = clock, policy_hash
        self.entries_retired=entries_retired

    def blockers(self, observation=None, *, purpose='ENTRY'):
        result = []
        if self.host != 'ORACLE': result.append('ORACLE_HOST_REQUIRED')
        if self.mode != 'live': result.append('LIVE_ENVIRONMENT_NOT_ACTIVATED')
        if self.pause_file.exists() or self.pause_file.is_symlink(): result.append('REPOSITORY_PAUSED')
        if not policy.intent(self.journal.store)['enabled']: result.append('OWNER_ALGO_OFF')
        proof = self.journal.store.meta('premium-execution-activation', {})
        if (not isinstance(proof, dict) or proof.get('format') != 'trading-execution-activation-v1'
                or proof.get('release') != self.release or not re.fullmatch('[a-f0-9]{40}', self.release)
                or proof.get('policy_hash') != self.policy_hash
                or any(proof.get(k) is not True for k in ('owner_approved', 'replay_verified',
                    'static_ip_verified', 'broker_write_verified', 'persistent_gtt_verified', 'child_link_verified'))):
            result.append('REVIEWED_LIVE_ACTIVATION_REQUIRED')
        if purpose not in ('ENTRY', 'ROLL', 'EXIT', 'PROTECT'):
            result.append('KNOWN_EXECUTION_PURPOSE_REQUIRED')
        if purpose in ('ENTRY','ROLL') and self.entries_retired:
            result.append('RETIRED_STRATEGY_NEW_ENTRY_DISABLED')
        if purpose in ('ENTRY', 'ROLL') and not policy.entry_window(self.clock()):
            result.append('OUTSIDE_1400_1900_JST')
        if observation is None:
            result.append('FRESH_COMPLETE_BROKER_STATE_REQUIRED')
        else:
            try:
                if observation.get('complete') is not True or not fresh(observation['received_at'], self.clock(), 10):
                    result.append('FRESH_COMPLETE_BROKER_STATE_REQUIRED')
            except (KeyError, ValueError, TypeError):
                result.append('FRESH_COMPLETE_BROKER_STATE_REQUIRED')
        return result

    def check(self, observation, *, purpose='ENTRY'):
        reasons = self.blockers(observation, purpose=purpose)
        if reasons: raise ExecutionDenied(reasons[0])

    @contextlib.contextmanager
    def authorize(self, method, path, body, observation, *, purpose):
        """Permit exactly one SDK request; recheck the gates at network time."""
        self.check(observation, purpose=purpose)
        if not write_route(method, path): raise PermissionError('UNSUPPORTED_ORDER_WRITE_ROUTE')
        value = dict(method=method, path=path, fingerprint=identity(body), gate=self,
                     observation=observation, purpose=purpose, used=False)
        token = _grant.set(value)
        try: yield
        finally: _grant.reset(token)


def write_route(method, path):
    return ((method == 'POST' and path in ('/v1/order/create', '/v1/order/cancel', '/v1/order/modify',
                                          '/v1/order-advance/create'))
            or (method == 'PUT' and re.fullmatch(r'/v1/order-advance/modify/[A-Za-z0-9_-]{1,128}', path))
            or (method == 'POST' and re.fullmatch(r'/v1/order-advance/cancel/FNO/GTT/[A-Za-z0-9_-]{1,128}', path)))


def consume_write(method, url, kwargs):
    value = _grant.get()
    if value is None: return False
    parsed = urlsplit(url)
    if (value['used'] or (parsed.scheme, parsed.netloc) != ('https', 'api.groww.in')
            or parsed.query or parsed.fragment or kwargs.get('params') or kwargs.get('data')
            or method.upper() != value['method'] or parsed.path != value['path']
            or identity(kwargs.get('json')) != value['fingerprint']):
        return False
    value['gate'].check(value['observation'], purpose=value['purpose'])
    value['used'] = True
    return True

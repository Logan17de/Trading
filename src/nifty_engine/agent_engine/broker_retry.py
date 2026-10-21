"""Small durable connection retry budget. Contains no tokens or credentials."""
from __future__ import annotations

from datetime import timedelta

from .contracts import stamp

KEY = 'groww-connection-retry-v1'
WINDOW = timedelta(hours=24)
MAX_TOKEN_ATTEMPTS = 48  # Local ceiling; other clients' provider usage is unknown.


class LocalAuthBudgetExceeded(ConnectionError):
    pass


class BrokerRetry:
    def __init__(self, store):
        self.store = store
        self.value = store.meta(KEY, dict(attempts=[], failures=0))
        if (not isinstance(self.value, dict) or
                not isinstance(self.value.get('attempts'), list) or
                len(self.value['attempts']) > MAX_TOKEN_ATTEMPTS or
                type(self.value.get('failures')) is not int or self.value['failures'] < 0):
            raise ValueError('BROKER_RETRY_STATE_INVALID')
        for at in self.value['attempts']:
            stamp(at)
        if self.value.get('retry_at'):
            stamp(self.value['retry_at'])

    @property
    def error(self):
        return self.value.get('failure')

    def ready(self, now):
        at = self.value.get('retry_at')
        return not at or now >= stamp(at)

    def reserve_token(self, now):
        # Commit before the HTTP request. Crashes/restarts do not reset usage.
        recent = sorted((at for at in self.value['attempts'] if stamp(at) > now - WINDOW), key=stamp)
        if len(recent) >= MAX_TOKEN_ATTEMPTS:
            self.value.update(attempts=recent, reason='LOCAL_AUTH_BUDGET',
                retry_at=(stamp(recent[0]) + WINDOW).isoformat(),
                failure=dict(ok=False, error_type='LocalAuthBudgetExceeded', code=None))
            self.store.set_meta(KEY, self.value)
            raise LocalAuthBudgetExceeded()
        self.value['attempts'] = recent + [now.isoformat()]
        self.store.set_meta(KEY, self.value)

    def failed(self, error, now, *, stage, retry_after_seconds=None):
        if error.get('error_type') == 'LocalAuthBudgetExceeded':
            return  # reserve_token already persisted the exact budget boundary.
        same = self.error and self.error.get('code') == error.get('code')
        failures = min(20, self.value['failures'] + 1 if same else 1)
        code = error.get('code')
        if code == '429':
            delay = (300, 900, 1800, 3600)[min(failures - 1, 3)]
            reason = 'PROVIDER_RATE_LIMIT'
        elif code in ('401', '403'):
            delay, reason = 900, 'APPROVAL_OR_ACCESS_REQUIRED'
        else:
            delay, reason = min(900, 60 * 2 ** min(failures - 1, 4)), 'CONNECTION_RETRY'
        if type(retry_after_seconds) in (int, float) and 0 <= retry_after_seconds <= 604800:
            delay = max(delay, retry_after_seconds)
        self.value.update(failures=failures, failure=error, failed_at=now.isoformat(),
            retry_at=(now + timedelta(seconds=delay)).isoformat(), stage=stage, reason=reason)
        self.store.set_meta(KEY, self.value)

    def succeeded(self, now):
        # Only a completed successful collection clears the fault, never a
        # heartbeat, skipped tick, key-page status, or newly generated token.
        if self.error:
            self.value.update(failures=0, failure=None, retry_at=None, reason=None,
                recovered_at=now.isoformat())
            self.store.set_meta(KEY, self.value)

    def public(self, now, *, connected):
        return dict(status=self.value.get('reason') if self.error else
                    'CONNECTED' if connected else 'NOT_CONNECTED',
            failure=self.error, next_retry_at=self.value.get('retry_at') if self.error else None,
            failed_at=self.value.get('failed_at') if self.error else None,
            stage=self.value.get('stage') if self.error else None,
            local_token_attempts_24h=sum(stamp(at) > now - WINDOW for at in self.value['attempts']),
            local_token_attempt_limit=MAX_TOKEN_ATTEMPTS,
            provider_remaining_quota=None, broker_writes=False)

"""Exact SDK transport and durable standard orders behind Oracle activation.

The HTTP guard remains read-only except for a single, scoped request authorized
by ExecutionGate. No desktop command exposes this transport or activation.
"""
from __future__ import annotations

import contextlib
import contextvars

from .execution import PreparedOrderGateway


class GrowwOrderTransport:
    simulated = False

    def __init__(self, market, gate):
        self.market, self.gate = market, gate
        self.context = contextvars.ContextVar('groww_order_context', default=None)

    @contextlib.contextmanager
    def scope(self, observation, purpose):
        token = self.context.set((observation, purpose))
        try: yield
        finally: self.context.reset(token)

    def _write(self, method, path, body, sdk_method, args):
        current = self.context.get()
        if current is None: raise PermissionError('EXECUTOR_SCOPE_REQUIRED')
        self.market.limiter.wait()
        with self.gate.authorize(method, path, body, current[0], purpose=current[1]):
            return sdk_method(**args)

    def _read(self, method, args):
        self.market.limiter.wait()
        return method(**args)

    def place_order(self, **args):
        body = {k:v for k,v in args.items() if k != 'timeout'}
        body.setdefault('trigger_price', None)
        return self._write('POST', '/v1/order/create', body, self.market.groww.place_order, args)

    def cancel_order(self, **args):
        return self._write('POST', '/v1/order/cancel', {k:v for k,v in args.items() if k != 'timeout'},
                           self.market.groww.cancel_order, args)

    def get_order_status_by_reference(self, **args):
        return self._read(self.market.groww.get_order_status_by_reference, args)

    def get_order_detail(self, **args):
        return self._read(self.market.groww.get_order_detail, args)

    def create_smart_order(self, **args):
        body = {k:v for k,v in args.items() if k != 'timeout' and v is not None}
        return self._write('POST', '/v1/order-advance/create', body, self.market.groww.create_smart_order, args)

    def modify_smart_order(self, **args):
        body = {k:v for k,v in args.items() if k not in ('timeout','smart_order_id') and v is not None}
        return self._write('PUT', '/v1/order-advance/modify/'+args['smart_order_id'], body,
                           self.market.groww.modify_smart_order, args)

    def cancel_smart_order(self, **args):
        return self._write('POST', '/v1/order-advance/cancel/FNO/GTT/'+args['smart_order_id'], None,
                           self.market.groww.cancel_smart_order, args)

    def get_smart_order(self, **args):
        return self._read(self.market.groww.get_smart_order, args)

    def get_smart_order_list(self, **args):
        return self._read(self.market.groww.get_smart_order_list, args)


class OracleOrderGateway(PreparedOrderGateway):
    """Reuse exact reservations, acknowledgment, fills and uncertain-write recovery."""
    def __init__(self, journal, broker, gate):
        super().__init__(journal, broker, gate.pause_file, deployed=True, clock=gate.clock)
        self.gate, self.purpose = gate, 'ENTRY'

    def _gate(self, observation):
        self.gate.check(observation, purpose=self.purpose)

    @contextlib.contextmanager
    def scope(self, observation, purpose):
        old = self.purpose; self.purpose = purpose
        try:
            with self.broker.scope(observation, purpose): yield
        finally: self.purpose = old

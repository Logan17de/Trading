"""Bounded GET-only OCO/GTT observations for chart SL/target overlays.

SDK 1.5.0 includes smart-order methods. This bounded REST reader uses the same
in-memory token and retains GET-only chart observation; it never arms an order.
"""
from __future__ import annotations

import re
from datetime import timedelta

from .contracts import IST


class SmartOrderReader:
    def __init__(self, broker, limiter):
        self.broker, self.limiter = broker, limiter
        self.cached, self.next_at = [], 0

    def _get(self, path, params=None):
        import requests
        self.limiter.wait()
        with requests.get("https://api.groww.in"+path,params=params,
            headers={"Authorization":"Bearer "+self.broker.token,"X-API-VERSION":"1.0"},
            timeout=5,allow_redirects=False,stream=True) as response:
            response.raise_for_status()
            raw = bytearray()
            for chunk in response.iter_content(8192):
                raw.extend(chunk)
                if len(raw) > 256000: raise ValueError("smart response too large")
        import json
        value = json.loads(raw)
        if value.get("status") != "SUCCESS" or not isinstance(value.get("payload"),dict):
            raise ValueError("smart response unavailable")
        return value["payload"]

    def __call__(self, positions, now):
        if now.timestamp() < self.next_at:
            return self.cached
        symbols = {(r.get("trading_symbol"),r.get("exchange")) for r in positions if r.get("quantity")}
        rows, details = [], 0
        end = now.astimezone(IST)
        for kind in ("OCO", "GTT"):
            complete = False
            for page in range(4):
                body = self._get("/v1/order-advance/list",{"segment":"FNO","smart_order_type":kind,
                    "status":"ACTIVE","page":page,"page_size":50,
                    "start_date_time":(end-timedelta(days=28)).replace(tzinfo=None).isoformat(timespec="seconds"),
                    "end_date_time":end.replace(tzinfo=None).isoformat(timespec="seconds")})
                items = body.get("orders")
                if not isinstance(items,list) or len(items) > 50:
                    raise ValueError("smart pagination unverified")
                for row in items:
                    if not isinstance(row,dict): raise ValueError("invalid smart record")
                    # Some list responses contain only ID/type/status. Fetch their
                    # details before matching; never infer a contract from its ID.
                    if row.get("trading_symbol") and (row.get("trading_symbol"),row.get("exchange")) not in symbols:
                        continue
                    details += 1
                    if details > 40: raise ValueError("too many protective order details")
                    identifier = row.get("smart_order_id")
                    if not isinstance(identifier,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",identifier):
                        raise ValueError("invalid smart identity")
                    full = self._get("/v1/order-advance/status/FNO/"+kind+"/internal/"+identifier)
                    if full.get("smart_order_id") != identifier or full.get("smart_order_type") != kind:
                        raise ValueError("smart detail identity differs")
                    if (full.get("trading_symbol"),full.get("exchange")) not in symbols:
                        continue
                    full["segment"] = "FNO"  # Proven by the exact FNO detail route.
                    rows.append(full)
                if len(items) < 50:
                    complete = True
                    break
            if not complete: raise ValueError("smart order list truncated")
        self.cached, self.next_at = rows, now.timestamp()+5
        return rows

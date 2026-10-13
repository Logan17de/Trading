"""Publish minimal safety heartbeat to private owner Supabase, never orders.

The cloud monitor can notice an Oracle VM outage even if the Windows PC sleeps.
No credentials, symbols, prices, account identities or broker order IDs are sent.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .alert_cadence import position_risk

PROJECT="https://imirspxhbnerxknyynqx.supabase.co"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError("OUTBOUND_REDIRECT_DENIED")


def payload(view,now):
    exposure=position_risk(view,now)
    return {"source":"oracle","risk_state":exposure,
            "observed_at":now.isoformat()}


def publish(view, now, *, url=None, key=None, opener=None):
    """Single fixed table/URL. A failure never changes trading state."""
    url=(url if url is not None else os.environ.get("SUPABASE_URL","")).rstrip("/")
    key=key if key is not None else os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url != PROJECT or not isinstance(key,str) or not key:
        return "NOT_CONFIGURED"
    content=json.dumps(payload(view,now),separators=(",",":")).encode()
    headers={"Content-Type":"application/json","apikey":key,
             "Prefer":"resolution=merge-duplicates,return=minimal"}
    if key.count(".")==2:headers["Authorization"]="Bearer "+key
    request=urllib.request.Request(PROJECT+"/rest/v1/trading_safety_state?on_conflict=source",
                                   data=b"["+content+b"]",headers=headers,method="POST")
    try:
        client=opener or urllib.request.build_opener(NoRedirect).open
        with client(request,timeout=8) as response:
            if response.status not in (200,201,204):raise ValueError("HEARTBEAT_NOT_CONFIRMED")
        return "UPLOADED"
    except Exception:
        return "UPLOAD_UNAVAILABLE"

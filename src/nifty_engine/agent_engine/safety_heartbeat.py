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

from .alert_cadence import position_risk, current

PROJECT="https://imirspxhbnerxknyynqx.supabase.co"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ValueError("OUTBOUND_REDIRECT_DENIED")


def minimal(snapshot,basket,owner_test,now):
    """Use only last collected position evidence and two lightweight journal keys."""
    snapshot=snapshot if isinstance(snapshot,dict) else {}
    basket=basket if isinstance(basket,dict) else {}
    owner_test=owner_test if isinstance(owner_test,dict) else {}
    phase=basket.get('phase','IDLE')
    if phase not in ('IDLE','CLOSED',None):
        exposure='ACTIVE'
    elif owner_test.get('state') in ('RUNNING','ERROR') or owner_test.get('attention'):
        exposure='ACTIVE'
    else:
        observed=[]
        for path in (snapshot.get('ordered_options',[]),
                     (snapshot.get('execution_observation') or {}).get('positions',[])):
            if isinstance(path,list):observed.extend(path)
        exposure='UNKNOWN'
        for row in observed:
            if not isinstance(row,dict):continue
            quantity=row.get('quantity')
            if type(quantity) in (int,float) and quantity!=0:
                exposure='ACTIVE'
                break
        else:
            if (snapshot.get('positions_status')=='AVAILABLE'
                    and snapshot.get('orders_status')=='AVAILABLE'
                    and current(snapshot.get('finished_at'),now)):
                exposure='FLAT'
    return {'risk_state':exposure,'broker_writes':False}


def payload(view,now):
    exposure=(view.get('risk_state') if isinstance(view,dict) and
              view.get('risk_state') in ('ACTIVE','FLAT','UNKNOWN')
              else position_risk(view,now))
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
